"""Tests for ``LlamaCppSLMAdapter``.

Module under test: ``kchat-skills/compiler/llama_cpp_adapter.py``.
The adapter is **not** unit-tested against a real llama-server in CI;
all tests here use a fake transport (mocked
``urllib.request.urlopen``). See ``tools/run_guardrail_demo.py`` for
the full end-to-end exercise that does require a running server.
"""
from __future__ import annotations

import io
import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import jsonschema
import pytest

from llama_cpp_adapter import (  # type: ignore[import-not-found]
    BONSAI_MODEL_NAME,
    BONSAI_MODEL_URL,
    LlamaCppSLMAdapter,
    safe_fallback_output,
)
from slm_adapter import SLMAdapter  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _input(text: str = "hi") -> dict[str, Any]:
    return {
        "message": {
            "text": text,
            "lang_hint": "en",
            "has_attachment": False,
            "attachment_kinds": [],
            "quoted_from_user": False,
            "is_outbound": False,
        },
        "context": {
            "group_kind": "small_group",
            "group_age_mode": "mixed_age",
            "user_role": "member",
            "relationship_known": True,
            "locale": "en-US",
            "jurisdiction_id": None,
            "community_overlay_id": None,
            "is_offline": False,
        },
        "local_signals": {
            "url_risk": 0.0,
            "pii_patterns_hit": [],
            "scam_patterns_hit": [],
            "lexicon_hits": [],
            "media_descriptors": [],
        },
        "constraints": {
            "max_output_tokens": 600,
            "temperature": 0.0,
            "output_format": "json",
            "schema_id": "kchat.guardrail.output.v1",
        },
    }


class _FakeResponse:
    """File-like object stand-in for ``urllib.request.urlopen``'s return."""

    def __init__(self, payload: dict[str, Any]):
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


@contextmanager
def _stub_urlopen(payload: dict[str, Any]):
    captured: dict[str, Any] = {}

    def fake_urlopen(req, *args: Any, **kwargs: Any) -> _FakeResponse:  # noqa: ANN001
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["headers"] = dict(req.headers)
        captured["timeout"] = kwargs.get("timeout") or (
            args[0] if args else None
        )
        return _FakeResponse(payload)

    with patch(
        "llama_cpp_adapter.urllib.request.urlopen", side_effect=fake_urlopen
    ):
        yield captured


def _make_chat_completion(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "model": BONSAI_MODEL_NAME,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Protocol conformance.
# ---------------------------------------------------------------------------
def test_llama_cpp_adapter_satisfies_protocol():
    adapter = LlamaCppSLMAdapter()
    assert isinstance(adapter, SLMAdapter)


def test_llama_cpp_adapter_has_classify_method():
    adapter = LlamaCppSLMAdapter()
    assert callable(getattr(adapter, "classify", None))


def test_default_server_url_and_model_constants():
    adapter = LlamaCppSLMAdapter()
    assert adapter.server_url == "http://localhost:8080"
    assert adapter.model == BONSAI_MODEL_NAME
    assert "Bonsai-1.7B.gguf" in BONSAI_MODEL_URL


# ---------------------------------------------------------------------------
# Unreachable server -> SAFE fallback.
# ---------------------------------------------------------------------------
def test_unreachable_server_returns_safe(output_schema):
    adapter = LlamaCppSLMAdapter(
        server_url="http://127.0.0.1:1",  # guaranteed unused port
        compiled_prompt="dummy prompt",
        timeout_seconds=0.1,
    )
    out = adapter.classify(_input())
    jsonschema.validate(instance=out, schema=output_schema)
    assert out == safe_fallback_output()
    assert out["category"] == 0
    assert out["severity"] == 0


def test_unreachable_server_records_latency():
    adapter = LlamaCppSLMAdapter(
        server_url="http://127.0.0.1:1",
        timeout_seconds=0.1,
    )
    adapter.classify(_input())
    assert adapter.last_latency_ms >= 0.0


# ---------------------------------------------------------------------------
# Successful response -> parsed and schema-coerced output.
# ---------------------------------------------------------------------------
def test_parses_well_formed_chat_completion(output_schema):
    well_formed_output = {
        "severity": 3,
        "category": 7,
        "confidence": 0.81,
        "actions": {
            "label_only": False,
            "warn": True,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": ["URL_RISK", "SCAM_PATTERN"],
        "rationale_id": "scam_phishing_v1",
    }
    payload = _make_chat_completion(json.dumps(well_formed_output))
    adapter = LlamaCppSLMAdapter(compiled_prompt="prompt-text")
    with _stub_urlopen(payload) as captured:
        out = adapter.classify(_input())

    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 7
    assert out["severity"] == 3
    assert out["confidence"] == pytest.approx(0.81)
    assert out["actions"]["warn"] is True
    assert "URL_RISK" in out["reason_codes"]
    assert out["rationale_id"] == "scam_phishing_v1"

    # Verify the request shape.
    assert captured["url"].endswith("/v1/chat/completions")
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}
    # Compiled prompt is the system message.
    sys_msg = body["messages"][0]
    assert sys_msg["role"] == "system"
    assert sys_msg["content"] == "prompt-text"
    # The packed input is the user message — JSON-serialised.
    user_msg = body["messages"][1]
    assert user_msg["role"] == "user"
    parsed_user = json.loads(user_msg["content"])
    assert parsed_user["constraints"]["temperature"] == 0.0


def test_compiled_prompt_appears_in_request_body():
    adapter = LlamaCppSLMAdapter(compiled_prompt="<<COMPILED>>")
    payload = _make_chat_completion(
        json.dumps(
            {
                "severity": 0,
                "category": 0,
                "confidence": 0.1,
                "actions": {
                    "label_only": False,
                    "warn": False,
                    "strong_warn": False,
                    "critical_intervention": False,
                    "suggest_redact": False,
                },
                "reason_codes": [],
                "rationale_id": "ok",
            }
        )
    )
    with _stub_urlopen(payload) as captured:
        adapter.classify(_input())
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["messages"][0]["content"] == "<<COMPILED>>"


# ---------------------------------------------------------------------------
# Malformed responses -> SAFE fallback.
# ---------------------------------------------------------------------------
def test_non_json_content_falls_back_to_safe(output_schema):
    payload = _make_chat_completion("this is not JSON")
    adapter = LlamaCppSLMAdapter()
    with _stub_urlopen(payload):
        out = adapter.classify(_input())
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 0
    assert out["severity"] == 0


def test_missing_choices_falls_back_to_safe(output_schema):
    adapter = LlamaCppSLMAdapter()
    with _stub_urlopen({"id": "x"}):  # no `choices`
        out = adapter.classify(_input())
    jsonschema.validate(instance=out, schema=output_schema)
    assert out == safe_fallback_output()


def test_array_json_content_falls_back_to_safe(output_schema):
    # Model returns a JSON array (not an object) — must coerce to SAFE.
    payload = _make_chat_completion(json.dumps([1, 2, 3]))
    adapter = LlamaCppSLMAdapter()
    with _stub_urlopen(payload):
        out = adapter.classify(_input())
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 0


def test_out_of_range_category_falls_back_to_safe(output_schema):
    bad = {
        "severity": 0,
        "category": 99,  # out of [0,15]
        "confidence": 0.5,
        "actions": {
            "label_only": False,
            "warn": False,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": [],
        "rationale_id": "x",
    }
    payload = _make_chat_completion(json.dumps(bad))
    adapter = LlamaCppSLMAdapter()
    with _stub_urlopen(payload):
        out = adapter.classify(_input())
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 0


def test_missing_required_fields_filled_with_safe_defaults(output_schema):
    sparse = {"category": 5, "confidence": 0.7}
    payload = _make_chat_completion(json.dumps(sparse))
    adapter = LlamaCppSLMAdapter()
    with _stub_urlopen(payload):
        out = adapter.classify(_input())
    # Output is still schema-valid (severity defaulted, actions defaulted, etc).
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 5
    assert out["severity"] == 0
    assert all(v is False for v in out["actions"].values())
    assert out["rationale_id"]


def test_invalid_reason_codes_dropped(output_schema):
    payload_dict = {
        "severity": 2,
        "category": 7,
        "confidence": 0.7,
        "actions": {
            "label_only": True,
            "warn": False,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": [
            "URL_RISK",  # valid
            "NOT_A_REAL_REASON",  # dropped
            "URL_RISK",  # duplicate -> deduped
        ],
        "rationale_id": "scam_link_v1",
    }
    payload = _make_chat_completion(json.dumps(payload_dict))
    adapter = LlamaCppSLMAdapter()
    with _stub_urlopen(payload):
        out = adapter.classify(_input())
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["reason_codes"] == ["URL_RISK"]


def test_counter_updates_passed_through_when_well_formed(output_schema):
    payload_dict = {
        "severity": 2,
        "category": 5,
        "confidence": 0.7,
        "actions": {
            "label_only": True,
            "warn": False,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": [],
        "rationale_id": "harassment_v1",
        "counter_updates": [
            {"counter_id": "harassment_30d", "delta": 1},
            {"counter_id": "", "delta": 1},  # dropped (empty id)
            {"counter_id": "ok", "delta": "5"},  # dropped (non-int delta)
        ],
    }
    payload = _make_chat_completion(json.dumps(payload_dict))
    adapter = LlamaCppSLMAdapter()
    with _stub_urlopen(payload):
        out = adapter.classify(_input())
    jsonschema.validate(instance=out, schema=output_schema)
    assert out.get("counter_updates") == [
        {"counter_id": "harassment_30d", "delta": 1}
    ]


# ---------------------------------------------------------------------------
# Request shape constraints.
# ---------------------------------------------------------------------------
def test_request_uses_temperature_zero_and_json_response_format():
    payload = _make_chat_completion(
        json.dumps(
            {
                "severity": 0,
                "category": 0,
                "confidence": 0.1,
                "actions": {
                    "label_only": False,
                    "warn": False,
                    "strong_warn": False,
                    "critical_intervention": False,
                    "suggest_redact": False,
                },
                "reason_codes": [],
                "rationale_id": "ok",
            }
        )
    )
    adapter = LlamaCppSLMAdapter()
    with _stub_urlopen(payload) as captured:
        adapter.classify(_input())
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}
    # max_tokens follows the input contract's `constraints` block.
    assert body["max_tokens"] == 600
