"""7-step hybrid local pipeline — on-device guardrail runtime.

Spec reference: ARCHITECTURE.md "Hybrid Local Pipeline" (lines 252-281)
and PHASES.md Phase 3:

    1. Text normalization (Unicode NFKC, case fold, homoglyph map,
       transliteration).
    2. Deterministic local detectors (URL risk, PII patterns, scam
       patterns, lexicon matching, media descriptor signal extraction).
    3. Signal packaging into the SLM input contract.
    4. SLM contextual classification (tiny SLM, temperature 0.0).
    5. Severity / threshold policy enforcement.
    6. Local JSON output generation.
    7. Local expiring counter updates (device-local only).

The pipeline is fully offline-capable: no step requires network
access. The SLM receives the *original* text; normalization is used
only for detector matching.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

from counters import CounterStore  # type: ignore[import-not-found]
from metric_validator import (  # type: ignore[import-not-found]
    MetricReport,
    MetricThresholds,
    MetricValidator,
)
from slm_adapter import SLMAdapter  # type: ignore[import-not-found]
from threshold_policy import ThresholdPolicy  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Step 1 — Text normalization.
# ---------------------------------------------------------------------------
# A tiny reference homoglyph map — enough to exercise the contract.
# Real packs ship a language-specific map indexed by
# ``homoglyph_map_id`` from the overlay's normalization block.
DEFAULT_HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic lookalikes → Latin.
    "\u0430": "a",  # а -> a
    "\u0435": "e",  # е -> e
    "\u043e": "o",  # о -> o
    "\u0440": "p",  # р -> p
    "\u0441": "c",  # с -> c
    "\u0445": "x",  # х -> x
    "\u0456": "i",  # і -> i
    # Fullwidth digits → ASCII.
    "\uff10": "0",
    "\uff11": "1",
    "\uff12": "2",
    "\uff13": "3",
    "\uff14": "4",
    "\uff15": "5",
    "\uff16": "6",
    "\uff17": "7",
    "\uff18": "8",
    "\uff19": "9",
}


def normalize_text(
    text: str,
    *,
    nfkc: bool = True,
    case_fold: bool = True,
    homoglyph_map: dict[str, str] | None = None,
) -> str:
    """Pipeline step 1.

    Order: NFKC → case fold → homoglyph substitution. Transliteration
    refs are applied by language-specific detectors downstream; the
    generic pipeline only needs a consistent matching form.
    """
    out = text
    if nfkc:
        out = unicodedata.normalize("NFKC", out)
    if case_fold:
        out = out.casefold()
    hmap = DEFAULT_HOMOGLYPH_MAP if homoglyph_map is None else homoglyph_map
    if hmap:
        out = "".join(hmap.get(ch, ch) for ch in out)
    return out


# ---------------------------------------------------------------------------
# Step 2 — Deterministic local detectors.
# ---------------------------------------------------------------------------
# Patterns are conservative and deliberately simple. Real detectors
# live on-device with language-specific rules; this pipeline is
# tested with mock inputs and these patterns are sufficient to
# exercise the contract end-to-end.
_URL_RE = re.compile(
    r"""(?:https?://|www\.)[^\s<>"']{3,}""",
    re.IGNORECASE,
)
_HIGH_RISK_TLDS = {"zip", "mov", "top", "click", "country", "xyz"}
_HIGH_RISK_KEYWORDS = {"login", "verify", "account", "secure", "update"}

_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d[\d\-\s().]{7,}\d(?!\w)")),
    (
        "CREDIT_CARD",
        re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
    ),
]

_SCAM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "ADVANCE_FEE",
        re.compile(
            r"\b(?:wire|transfer|deposit)\b.*\bfee\b", re.IGNORECASE
        ),
    ),
    (
        "FAKE_GIVEAWAY",
        re.compile(
            r"\b(?:congratulations|you\s+(?:have\s+)?won|claim\s+your\s+prize)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "CREDENTIAL_HARVEST",
        re.compile(
            r"\b(?:verify|confirm|reset|update)\b.*\b(?:password|account|login)\b",
            re.IGNORECASE,
        ),
    ),
]


def score_url_risk(normalized_text: str) -> float:
    """Aggregate URL risk score in ``[0.0, 1.0]``."""
    urls = _URL_RE.findall(normalized_text)
    if not urls:
        return 0.0
    max_score = 0.0
    for url in urls:
        score = 0.2
        lowered = url.lower()
        for tld in _HIGH_RISK_TLDS:
            if lowered.endswith("." + tld) or ("." + tld + "/") in lowered:
                score = max(score, 0.9)
        for kw in _HIGH_RISK_KEYWORDS:
            if kw in lowered:
                score = max(score, 0.85)
        max_score = max(max_score, score)
    return min(1.0, max_score)


def detect_pii(normalized_text: str) -> list[str]:
    hits: list[str] = []
    for name, pattern in _PII_PATTERNS:
        if pattern.search(normalized_text):
            hits.append(name)
    return hits


def detect_scam(normalized_text: str) -> list[str]:
    hits: list[str] = []
    for name, pattern in _SCAM_PATTERNS:
        if pattern.search(normalized_text):
            hits.append(name)
    return hits


@dataclass
class LexiconEntry:
    """A single lexicon token bundled with its category + weight."""

    lexicon_id: str
    category: int
    tokens: list[str]
    weight: float = 0.6


def match_lexicons(
    normalized_text: str, lexicons: list[LexiconEntry]
) -> list[dict[str, Any]]:
    """Return ``local_signals.lexicon_hits``-shaped entries."""
    hits: list[dict[str, Any]] = []
    for lex in lexicons:
        for token in lex.tokens:
            if token and token.casefold() in normalized_text:
                hits.append(
                    {
                        "lexicon_id": lex.lexicon_id,
                        "category": lex.category,
                        "weight": lex.weight,
                    }
                )
                break  # one hit per lexicon is sufficient
    return hits


def extract_media_descriptors(
    media: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Pass-through, clamping numeric scores to the schema range.

    Real implementations derive ``nsfw_score`` / ``violence_score`` /
    ``face_count`` from on-device vision models. The pipeline only
    needs to guarantee the shape matches ``local_signal_schema.json``.
    """
    out: list[dict[str, Any]] = []
    for m in media or []:
        item: dict[str, Any] = {"kind": m.get("kind", "image")}
        for key in ("nsfw_score", "violence_score"):
            if key in m and m[key] is not None:
                item[key] = max(0.0, min(1.0, float(m[key])))
        if "face_count" in m and m["face_count"] is not None:
            item["face_count"] = max(0, int(m["face_count"]))
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Step 3 — Signal packaging (construct the input contract).
# ---------------------------------------------------------------------------
@dataclass
class SkillBundle:
    """Compiled skill bundle passed to the pipeline.

    The pipeline does not parse YAML; callers hand it the resolved
    configuration. ``jurisdiction_id`` / ``community_overlay_id`` are
    the values that end up in ``context`` of the input contract.
    ``lexicons`` are the deterministic-detector lexicon entries.
    ``normalization`` controls :func:`normalize_text`.
    """

    global_baseline_id: str = "kchat.global.guardrail.baseline"
    jurisdiction_id: Optional[str] = None
    community_overlay_id: Optional[str] = None
    lexicons: list[LexiconEntry] = field(default_factory=list)
    normalization: dict[str, Any] = field(
        default_factory=lambda: {
            "nfkc": True,
            "case_fold": True,
            "homoglyph_map_id": "homoglyph_core_v1",
            "transliteration_refs": ["translit_core_v1"],
        }
    )


def pack_signals(
    *,
    message: dict[str, Any],
    context: dict[str, Any],
    local_signals: dict[str, Any],
) -> dict[str, Any]:
    """Pipeline step 3: pack the structured ``kchat.guardrail.local_signal.v1``.

    The pipeline supplies the ``constraints`` block (temperature 0.0,
    max_output_tokens 600, output_format json, schema_id pinned) so the
    SLM adapter cannot drift them.
    """
    return {
        "message": message,
        "context": context,
        "local_signals": local_signals,
        "constraints": {
            "max_output_tokens": 600,
            "temperature": 0.0,
            "output_format": "json",
            "schema_id": "kchat.guardrail.output.v1",
        },
    }


# ---------------------------------------------------------------------------
# Pipeline orchestrator.
# ---------------------------------------------------------------------------
@dataclass
class GuardrailPipeline:
    """The 7-step hybrid local pipeline.

    Parameters
    ----------
    skill_bundle
        Compiled active skill bundle (global baseline + optional
        jurisdiction overlay + optional community overlay).
    slm_adapter
        Any :class:`SLMAdapter` implementation — the real model or
        :class:`MockSLMAdapter` in tests.
    threshold_policy
        Hard-coded threshold enforcer. Defaults to a fresh
        :class:`ThresholdPolicy`.
    counter_store
        Optional device-local expiring-counter store. When provided,
        step 7 applies ``counter_updates`` from the output.
    """

    skill_bundle: SkillBundle
    slm_adapter: SLMAdapter
    threshold_policy: ThresholdPolicy = field(default_factory=ThresholdPolicy)
    counter_store: Optional[CounterStore] = None

    def classify(
        self,
        message: dict[str, Any],
        context: dict[str, Any],
        *,
        group_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run all 7 pipeline steps and return the output JSON.

        ``message`` and ``context`` match the corresponding blocks of
        ``kchat.guardrail.local_signal.v1``. ``group_id`` is required
        only if the skill bundle configures a ``counter_store`` — it
        scopes counter updates to one group.
        """
        # --- Step 1: Normalize text.
        text = message.get("text", "") or ""
        norm = self.skill_bundle.normalization or {}
        normalized = normalize_text(
            text,
            nfkc=bool(norm.get("nfkc", True)),
            case_fold=bool(norm.get("case_fold", True)),
        )

        # --- Step 2: Deterministic local detectors.
        local_signals = {
            "url_risk": score_url_risk(normalized),
            "pii_patterns_hit": detect_pii(normalized),
            "scam_patterns_hit": detect_scam(normalized),
            "lexicon_hits": match_lexicons(
                normalized, self.skill_bundle.lexicons
            ),
            "media_descriptors": extract_media_descriptors(
                message.get("media_descriptors")
            ),
        }

        # --- Step 3: Signal packaging.
        packed_message = {
            "text": text,  # SLM receives *original* text, not normalized.
            "lang_hint": message.get("lang_hint"),
            "has_attachment": bool(message.get("has_attachment", False)),
            "attachment_kinds": list(message.get("attachment_kinds") or []),
            "quoted_from_user": bool(message.get("quoted_from_user", False)),
            "is_outbound": bool(message.get("is_outbound", False)),
        }
        packed_context = {
            "group_kind": context.get("group_kind", "small_group"),
            "group_age_mode": context.get("group_age_mode", "mixed_age"),
            "user_role": context.get("user_role", "member"),
            "relationship_known": bool(
                context.get("relationship_known", True)
            ),
            "locale": context.get("locale", "en-US"),
            "jurisdiction_id": (
                self.skill_bundle.jurisdiction_id
                if self.skill_bundle.jurisdiction_id is not None
                else context.get("jurisdiction_id")
            ),
            "community_overlay_id": (
                self.skill_bundle.community_overlay_id
                if self.skill_bundle.community_overlay_id is not None
                else context.get("community_overlay_id")
            ),
            "is_offline": bool(context.get("is_offline", False)),
        }
        packed = pack_signals(
            message=packed_message,
            context=packed_context,
            local_signals=local_signals,
        )

        # --- Step 4: SLM contextual classification.
        raw_output = self.slm_adapter.classify(packed)

        # --- Step 5: Severity / threshold policy enforcement.
        policy_output = self.threshold_policy.apply(raw_output)

        # --- Step 6: Local JSON output generation — already a dict,
        # just guarantee required keys exist.
        output = _finalise_output(policy_output)

        # --- Step 7: Counter updates.
        updates = output.get("counter_updates") or []
        if updates and self.counter_store is not None and group_id:
            self.counter_store.apply_counter_updates(group_id, updates)

        return output

    # ------------------------------------------------------------------
    # Compiler-side metric-validation hook.
    # ------------------------------------------------------------------
    def validate_metrics(
        self,
        test_cases: list[dict[str, Any]],
        *,
        thresholds: Optional[MetricThresholds] = None,
    ) -> MetricReport:
        """Run ``test_cases`` end-to-end and return a :class:`MetricReport`.

        Phase 4 compiler entry point — the compiler refuses to sign a
        skill-pack bundle whose ``MetricReport.passed`` is ``False``.
        ``test_cases`` follow the ``case_schema`` block of
        ``kchat-skills/tests/test_suite_template.yaml``.
        """
        validator = MetricValidator(
            thresholds=thresholds or MetricThresholds()
        )
        return validator.run_pipeline(self, test_cases)


def _finalise_output(output: dict[str, Any]) -> dict[str, Any]:
    """Guarantee the output dict has every required output_schema key."""
    required = {
        "severity",
        "category",
        "confidence",
        "actions",
        "reason_codes",
        "rationale_id",
    }
    for key in required:
        if key not in output:
            if key == "actions":
                output["actions"] = {
                    "label_only": False,
                    "warn": False,
                    "strong_warn": False,
                    "critical_intervention": False,
                    "suggest_redact": False,
                }
            elif key == "reason_codes":
                output["reason_codes"] = []
            elif key == "rationale_id":
                output["rationale_id"] = "safe_benign_v1"
            else:
                output[key] = 0
    return output


__all__ = [
    "DEFAULT_HOMOGLYPH_MAP",
    "GuardrailPipeline",
    "LexiconEntry",
    "SkillBundle",
    "detect_pii",
    "detect_scam",
    "extract_media_descriptors",
    "match_lexicons",
    "normalize_text",
    "pack_signals",
    "score_url_risk",
]
