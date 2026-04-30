"""llama.cpp ``SLMAdapter`` — runs the on-device guardrail SLM.

Spec references:

* PHASES.md Phase 3 — "Define the SLM runtime adapter interface — the
  boundary between the pipeline and any tiny-SLM backend (so we can
  swap backends without changing skill packs)."
* ARCHITECTURE.md "Hybrid Local Pipeline" step 4 — "SLM contextual
  classification (tiny SLM, temperature 0.0)".

This adapter is one concrete implementation of the
:class:`slm_adapter.SLMAdapter` Protocol. It targets a running
`llama-server` from the `kennguy3n/llama.cpp` fork (branch ``prism``)
exposed over the OpenAI-compatible ``/v1/chat/completions`` endpoint
and is intended for use with the **Bonsai-1.7B** GGUF model:

    https://huggingface.co/prism-ml/Bonsai-1.7B-gguf/resolve/main/Bonsai-1.7B.gguf

Design constraints inherited from the SLMAdapter contract:

* **Deterministic.** Sends ``temperature=0.0`` on every call so the
  guardrail pipeline produces identical output for identical input.
* **JSON-only output.** Sends ``response_format={"type":"json_object"}``
  to keep the model on-rail; the runtime only ships the constrained
  output schema.
* **Privacy-safe fallback.** If the server is unreachable, returns a
  malformed JSON body, or returns out-of-range fields, the adapter
  falls back to a SAFE output (category 0, severity 0) rather than
  raising or returning unsafe defaults. This matches the pipeline's
  existing safety-first design (see ``threshold_policy.coerce_to_safe``).
* **Stdlib only.** No ``requests`` / ``httpx`` dependency — uses
  :mod:`urllib.request` so the runtime adapter can ship with the same
  zero-dependency footprint as the rest of the compiler module.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from slm_adapter import (  # type: ignore[import-not-found]
    CAT_SAFE,
    SLMAdapter,
)


# Default Bonsai-1.7B GGUF download URL (huggingface "resolve" endpoint).
BONSAI_MODEL_URL = (
    "https://huggingface.co/prism-ml/Bonsai-1.7B-gguf/resolve/main/Bonsai-1.7B.gguf"
)
BONSAI_MODEL_NAME = "Bonsai-1.7B"

# Output-schema bounds. Mirror ``kchat-skills/global/output_schema.json``.
_VALID_REASON_CODES = frozenset(
    {
        "LEXICON_HIT",
        "SCAM_PATTERN",
        "PRIVATE_DATA_PATTERN",
        "URL_RISK",
        "QUOTED_SPEECH_CONTEXT",
        "NEWS_CONTEXT",
        "EDUCATION_CONTEXT",
        "COUNTERSPEECH_CONTEXT",
        "GROUP_AGE_MODE",
        "JURISDICTION_OVERRIDE",
        "COMMUNITY_RULE",
        "CHILD_SAFETY_FLOOR",
    }
)

_ACTION_KEYS = (
    "label_only",
    "warn",
    "strong_warn",
    "critical_intervention",
    "suggest_redact",
)


def _zero_actions() -> dict[str, bool]:
    return {key: False for key in _ACTION_KEYS}


def safe_fallback_output() -> dict[str, Any]:
    """Return a SAFE ``kchat.guardrail.output.v1``-shaped dict.

    Used whenever the model is unreachable or returns garbage. Matches
    the privacy-first invariant: when in doubt, the runtime degrades to
    SAFE rather than to a permissive label.
    """
    return {
        "severity": 0,
        "category": CAT_SAFE,
        "confidence": 0.05,
        "actions": _zero_actions(),
        "reason_codes": [],
        "rationale_id": "llama_cpp_safe_fallback_v1",
    }


# ---------------------------------------------------------------------------
# Adapter.
# ---------------------------------------------------------------------------
@dataclass
class LlamaCppSLMAdapter:
    """SLMAdapter backed by a running llama.cpp ``llama-server``.

    Parameters
    ----------
    server_url
        Base URL of the running ``llama-server`` (default
        ``http://localhost:8080``). The adapter posts to
        ``{server_url}/v1/chat/completions``.
    compiled_prompt
        The compiled skill-pack prompt (system message). Typically the
        result of ``SkillPackCompiler.compile(...).text``. Must already
        respect the 1800-token instruction budget.
    timeout_seconds
        Per-request HTTP timeout. Default is 30 s; the pipeline's p95
        latency target is 250 ms but generation can occasionally take
        longer on the first call after a model load.
    model
        Optional model name passed to the server (mostly cosmetic for
        ``llama-server``; defaults to ``Bonsai-1.7B``).
    logger
        Optional :class:`logging.Logger` used for structured per-call
        latency / error logging. If ``None``, the module logger is
        used.

    Latency
    -------
    The most recent call's wall-clock latency in milliseconds is stored
    in :attr:`last_latency_ms` so callers (the demo / benchmark
    scripts) can record per-call timings without instrumenting the
    pipeline.
    """

    server_url: str = "http://localhost:8080"
    compiled_prompt: str = ""
    timeout_seconds: float = 30.0
    model: str = BONSAI_MODEL_NAME
    logger: Optional[logging.Logger] = None
    last_latency_ms: float = field(default=0.0, init=False)

    # ------------------------------------------------------------------
    # Public API.
    # ------------------------------------------------------------------
    def classify(self, input: dict[str, Any]) -> dict[str, Any]:
        """Run the SLM over ``input`` and return a validated output dict.

        Returns :func:`safe_fallback_output` whenever the server is
        unreachable, returns malformed JSON, or returns an output that
        cannot be coerced into the output schema.
        """
        log = self.logger or _module_logger
        start = time.perf_counter()
        try:
            raw = self._request_completion(input)
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            ConnectionError,
            OSError,
        ) as exc:
            self.last_latency_ms = (time.perf_counter() - start) * 1000.0
            log.warning(
                "llama-server unreachable (%s); falling back to SAFE", exc
            )
            return safe_fallback_output()
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self.last_latency_ms = (time.perf_counter() - start) * 1000.0
            log.warning(
                "llama-server transport error (%s); falling back to SAFE",
                exc,
            )
            return safe_fallback_output()

        self.last_latency_ms = (time.perf_counter() - start) * 1000.0

        content = _extract_message_content(raw)
        if content is None:
            log.warning("llama-server response missing message content")
            return safe_fallback_output()

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            log.warning("llama-server returned non-JSON content: %s", exc)
            return safe_fallback_output()

        if not isinstance(parsed, dict):
            log.warning("llama-server JSON content is not an object")
            return safe_fallback_output()

        return _coerce_to_output_schema(parsed)

    # ------------------------------------------------------------------
    # Internals.
    # ------------------------------------------------------------------
    def _build_request_body(self, input: dict[str, Any]) -> dict[str, Any]:
        """Construct the OpenAI-compatible chat-completions body.

        The compiled prompt becomes the **system** message; the packed
        ``kchat.guardrail.local_signal.v1`` instance is JSON-serialised
        and becomes the **user** message. ``temperature=0.0`` and
        ``response_format={"type":"json_object"}`` come straight from
        ARCHITECTURE.md / the input contract's ``constraints`` block.
        """
        constraints = input.get("constraints") or {}
        max_tokens = int(constraints.get("max_output_tokens") or 600)
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.compiled_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        input, ensure_ascii=False, sort_keys=True
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

    def _request_completion(self, input: dict[str, Any]) -> dict[str, Any]:
        endpoint = self.server_url.rstrip("/") + "/v1/chat/completions"
        body = self._build_request_body(input)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 — explicit user-configured URL
            endpoint,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(  # noqa: S310 — see above
            req, timeout=self.timeout_seconds
        ) as response:
            payload = response.read().decode("utf-8")
        return json.loads(payload)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
_module_logger = logging.getLogger("kchat.guardrail.llama_cpp")


def _extract_message_content(raw: dict[str, Any]) -> Optional[str]:
    """Extract ``choices[0].message.content`` from a chat-completions reply."""
    choices = raw.get("choices") if isinstance(raw, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    return content


def _coerce_to_output_schema(parsed: dict[str, Any]) -> dict[str, Any]:
    """Validate / normalise a model JSON output against the output schema.

    Missing or out-of-range required fields are filled with safe
    defaults rather than raising. Any malformed structure that cannot
    be repaired collapses to :func:`safe_fallback_output`.
    """
    try:
        category = int(parsed.get("category", CAT_SAFE))
    except (TypeError, ValueError):
        return safe_fallback_output()
    if not 0 <= category <= 15:
        return safe_fallback_output()

    try:
        severity = int(parsed.get("severity", 0))
    except (TypeError, ValueError):
        return safe_fallback_output()
    if not 0 <= severity <= 5:
        return safe_fallback_output()

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        return safe_fallback_output()
    if not 0.0 <= confidence <= 1.0:
        return safe_fallback_output()

    actions_in = parsed.get("actions") or {}
    if not isinstance(actions_in, dict):
        actions_in = {}
    actions = _zero_actions()
    for key in _ACTION_KEYS:
        if isinstance(actions_in.get(key), bool):
            actions[key] = actions_in[key]

    reason_codes_in = parsed.get("reason_codes") or []
    if not isinstance(reason_codes_in, list):
        reason_codes_in = []
    reason_codes = [
        rc
        for rc in reason_codes_in
        if isinstance(rc, str) and rc in _VALID_REASON_CODES
    ]
    # Deduplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for rc in reason_codes:
        if rc not in seen:
            seen.add(rc)
            deduped.append(rc)

    rationale_id = parsed.get("rationale_id")
    if not isinstance(rationale_id, str) or not rationale_id:
        rationale_id = "llama_cpp_default_rationale_v1"

    out: dict[str, Any] = {
        "severity": severity,
        "category": category,
        "confidence": confidence,
        "actions": actions,
        "reason_codes": deduped,
        "rationale_id": rationale_id,
    }

    resource_link_id = parsed.get("resource_link_id")
    if isinstance(resource_link_id, str) and resource_link_id:
        out["resource_link_id"] = resource_link_id

    counter_updates_in = parsed.get("counter_updates") or []
    if isinstance(counter_updates_in, list):
        cleaned: list[dict[str, Any]] = []
        for upd in counter_updates_in:
            if not isinstance(upd, dict):
                continue
            counter_id = upd.get("counter_id")
            delta = upd.get("delta")
            if (
                isinstance(counter_id, str)
                and counter_id
                and isinstance(delta, int)
                and not isinstance(delta, bool)
            ):
                cleaned.append({"counter_id": counter_id, "delta": delta})
        if cleaned:
            out["counter_updates"] = cleaned

    return out


# Make ``isinstance(adapter, SLMAdapter)`` cheap for tests that import
# the protocol.
_PROTOCOL_REFERENCE: SLMAdapter = LlamaCppSLMAdapter()  # type: ignore[assignment]
del _PROTOCOL_REFERENCE


__all__ = [
    "BONSAI_MODEL_NAME",
    "BONSAI_MODEL_URL",
    "LlamaCppSLMAdapter",
    "safe_fallback_output",
]
