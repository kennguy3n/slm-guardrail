"""7-step hybrid local pipeline — on-device guardrail runtime.

Spec reference: ARCHITECTURE.md "Hybrid Local Pipeline" (lines 252-281)
and PHASES.md Phase 3:

    1. Text normalization (Unicode NFKC, case fold, homoglyph map,
       transliteration).
    2. Deterministic local detectors (URL risk, PII patterns, scam
       patterns, lexicon matching, media descriptor signal extraction).
    3. Signal packaging into the classifier input contract.
    4. Encoder-based contextual classification
       (XLM-R, deterministic argmax over fixed prototype
       embeddings).
    5. Severity / threshold policy enforcement.
    6. Local JSON output generation.
    7. Local expiring counter updates (device-local only).

The pipeline is fully offline-capable: no step requires network
access. The encoder receives the *original* text; normalization is
used only for detector matching.
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
from encoder_adapter import (  # type: ignore[import-not-found]
    HEALTH_TO_MODEL_HEALTH_OUTPUT,
    EncoderAdapter,
)
from threshold_policy import ThresholdPolicy  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Step 1 — Text normalization.
# ---------------------------------------------------------------------------
# Reference homoglyph map.
#
# Production packs SHOULD ship a language-specific, regularly-updated
# map indexed by ``homoglyph_map_id`` from the overlay's normalization
# block. The set below is a curated subset of Unicode TR39
# (https://www.unicode.org/reports/tr39/) confusables covering the
# scripts most often used for adversarial obfuscation of Latin text
# in chat messages:
#
# * Cyrillic → Latin (uppercase + lowercase confusables)
# * Greek → Latin (uppercase + lowercase confusables)
# * Fullwidth digits / Latin letters → ASCII
# * Mathematical alphanumeric symbols are intentionally NOT included
#   here — they should be removed by ``NFKC`` in :func:`normalize_text`
#   before the homoglyph map runs.
#
# This is a reference implementation. Real deployments should:
#
# * Pull the full TR39 confusable table at build time and version it.
# * Track Unicode revisions and emit telemetry for unmapped codepoints.
# * Handle script-mixing detection (e.g. a single word that mixes
#   Cyrillic and Latin characters is highly suspicious independent of
#   the actual codepoints).
#
# TODO(production): replace this hand-curated map with a generated
# table sourced from ``confusables.txt`` (Unicode TR39 SA "Confusable
# Detection"). Track Unicode version in the homoglyph_map_id.
DEFAULT_HOMOGLYPH_MAP: dict[str, str] = {
    # ----- Cyrillic → Latin (lowercase). -----
    "\u0430": "a",  # а -> a
    "\u0432": "b",  # в -> b (small Cyrillic ve looks like Latin small b in some fonts)
    "\u0435": "e",  # е -> e
    "\u04bb": "h",  # һ -> h (Cyrillic shha)
    "\u043a": "k",  # к -> k
    "\u043c": "m",  # м -> m
    "\u043d": "h",  # н -> h (Cyrillic en looks like Latin H)
    "\u043e": "o",  # о -> o
    "\u0440": "p",  # р -> p
    "\u0441": "c",  # с -> c
    "\u0442": "t",  # т -> t
    "\u0443": "y",  # у -> y
    "\u0445": "x",  # х -> x
    "\u0456": "i",  # і -> i
    "\u0458": "j",  # ј -> j
    # ----- Cyrillic → Latin (uppercase). -----
    "\u0410": "a",  # А -> A
    "\u0412": "b",  # В -> B
    "\u0415": "e",  # Е -> E
    "\u041a": "k",  # К -> K
    "\u041c": "m",  # М -> M
    "\u041d": "h",  # Н -> H
    "\u041e": "o",  # О -> O
    "\u0420": "p",  # Р -> P
    "\u0421": "c",  # С -> C
    "\u0422": "t",  # Т -> T
    "\u0423": "y",  # У -> Y
    "\u0425": "x",  # Х -> X
    "\u0406": "i",  # І -> I (Cyrillic byelorussian-ukrainian I)
    "\u0408": "j",  # Ј -> J
    # ----- Greek → Latin (uppercase). -----
    "\u0391": "a",  # Α -> A
    "\u0392": "b",  # Β -> B
    "\u0395": "e",  # Ε -> E
    "\u0396": "z",  # Ζ -> Z
    "\u0397": "h",  # Η -> H
    "\u0399": "i",  # Ι -> I
    "\u039a": "k",  # Κ -> K
    "\u039c": "m",  # Μ -> M
    "\u039d": "n",  # Ν -> N
    "\u039f": "o",  # Ο -> O
    "\u03a1": "p",  # Ρ -> P
    "\u03a4": "t",  # Τ -> T
    "\u03a5": "y",  # Υ -> Y
    "\u03a7": "x",  # Χ -> X
    # ----- Greek → Latin (lowercase). -----
    # NOTE: ``normalize_text`` applies ``casefold()`` before this map,
    # so the uppercase Greek entries above are only there to document
    # intent — at runtime they have already been folded to lowercase
    # by the time the map is applied. The lowercase entries here are
    # the ones that actually do the work. Keep both in sync.
    "\u03b1": "a",  # α -> a
    "\u03b2": "b",  # β -> b
    "\u03b5": "e",  # ε -> e
    "\u03b6": "z",  # ζ -> z
    "\u03b7": "h",  # η -> h
    "\u03b9": "i",  # ι -> i (Greek small iota)
    "\u03ba": "k",  # κ -> k
    "\u03bc": "m",  # μ -> m
    "\u03bd": "v",  # ν -> v
    "\u03bf": "o",  # ο -> o
    "\u03c1": "p",  # ρ -> p
    "\u03c4": "t",  # τ -> t
    "\u03c5": "y",  # υ -> y
    "\u03c7": "x",  # χ -> x
    # ----- Fullwidth digits → ASCII. -----
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
#
# TODO(production): URL risk in this module is a reference detector.
# A shippable URL detector should add at least the following:
#
# * Domain reputation lookups (offline allowlist / blocklist; never
#   network at runtime). Ship the lists in the skill pack and update
#   them through the pack rollout system.
# * Redirect-chain analysis — the URL preview / unfurler is in the
#   chat client; surface the *final* landing URL into ``local_signals``
#   rather than just the link text so the detector can score the real
#   destination.
# * Homoglyph domain detection — score domains whose Punycode form
#   differs from their displayed form ("aрple.com" vs "apple.com").
# * URL shortener provenance — known shorteners (bit.ly, t.co, ...)
#   get a small score bump because they hide the destination from the
#   detector.
# * Lookalike registrar TLD scoring — some TLDs are operated by
#   abusive registrars and should carry a base score even with no
#   keywords; this module already encodes a small example set in
#   ``_HIGH_RISK_TLDS`` but production should source it from the
#   skill-pack data block.
_URL_RE = re.compile(
    r"""(?:https?://|www\.)[^\s<>"']{3,}""",
    re.IGNORECASE,
)
_HIGH_RISK_TLDS = {"zip", "mov", "top", "click", "country", "xyz"}
_HIGH_RISK_KEYWORDS = {"login", "verify", "account", "secure", "update"}

# TODO(production): PII detection in this module is a reference
# detector. A shippable PII detector should add at least the
# following:
#
# * Country-specific phone formats: NANP (US/CA/Canada), E.164,
#   national prefixes, dial codes, and the dozens of country-specific
#   short-code conventions. The generic ``PHONE`` regex below is
#   intentionally permissive and matches ordinary numeric strings.
# * National identifier patterns: SSN (US), NINO (UK), Aadhaar (IN),
#   personnummer (SE), CPF/CNPJ (BR), CURP (MX), KTP (ID), HKID (HK),
#   and so on — each with a check-digit validator where applicable.
# * Financial identifiers: IBAN (with country-specific length and
#   mod-97 check), SWIFT/BIC, SEPA/ACH routing numbers.
# * Government issued document numbers: passports, driver's licences,
#   tax IDs.
# * Health identifiers (where legally permissible): NHS number,
#   Medicare number.
# * Address blocks via NER — regex cannot detect free-form addresses
#   reliably; a small on-device span classifier is appropriate.
_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d[\d\-\s().]{7,}\d(?!\w)")),
    (
        "CREDIT_CARD",
        re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
    ),
]

# TODO(production): scam pattern detection here is a reference
# detector. A shippable scam detector should add at least the
# following families:
#
# * Romance scam patterns — long-form relationship-establishing
#   pretext + money request (``can you help me with``,
#   ``my crew/ship is stuck``, requests for gift cards, requests to
#   move conversation off-platform).
# * Crypto scam patterns — ``send eth/btc/usdt`` + ``wallet address``,
#   pump-and-dump signals, fake giveaway with ``send X get 2X``
#   patterns.
# * QR scam patterns — attachments with ``scan this QR``,
#   ``parking ticket QR``, ``EV charger QR``.
# * Domain lookalike + brand-impersonation patterns —
#   ``account-verify-{brand}.com`` style, especially in combination
#   with the URL detector.
# * Pig-butchering / investment-scam patterns — ``guaranteed returns``,
#   ``exclusive opportunity``, ``my financial advisor``.
# * Tech-support scam patterns — ``virus detected``,
#   ``call this number``, ``Microsoft/Apple support``.
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
# Step 2 (auxiliary) — Protected-speech context hints.
# ---------------------------------------------------------------------------
# Community overlays whose presence implies the message lives in a
# protected-speech context. Substring match against
# ``context.community_overlay_id`` (case-insensitive).
_NEWS_CONTEXT_OVERLAY_TOKENS: tuple[str, ...] = (
    "journalism",
    "news",
)
_EDUCATION_CONTEXT_OVERLAY_TOKENS: tuple[str, ...] = (
    "education_higher",
    "education",
    "school",
    "research",
    "science",
)
_COUNTERSPEECH_CONTEXT_OVERLAY_TOKENS: tuple[str, ...] = (
    "lgbtq_support",
    "minority_support",
    "civic",
    "humanrights",
)


# Default confidence assigned to each context hint kind. P1-1: the
# threshold policy reads these per-hint confidences and demotes only
# when the supporting context is strong enough (see
# ``CONTEXT_DEMOTION_CONFIDENCE_THRESHOLD`` below). Production
# overlays should override these via a per-overlay confidence table
# rather than relying on the defaults here.
#
# * QUOTED_SPEECH_CONTEXT is structurally observable — it comes from
#   ``message.quoted_from_user`` which the chat client sets when the
#   user explicitly quotes another message — so it gets the highest
#   default confidence.
# * NEWS_CONTEXT / EDUCATION_CONTEXT / COUNTERSPEECH_CONTEXT are
#   derived from a *coarse substring match* on the community overlay
#   id. They are easy to spoof and production should not treat them
#   as definitive demotion grounds. The defaults below sit just at
#   the demotion floor so an overlay-id-only signal still preserves
#   pre-P1-1 'protected-speech full demote' behaviour; **production
#   deployments are strongly encouraged to override these values
#   downward** (e.g. 0.3) so overlay-only context can only soften to
#   'warn with context' rather than fully suppress harm labels.
CONTEXT_HINT_CONFIDENCE: dict[str, float] = {
    "QUOTED_SPEECH_CONTEXT": 0.7,
    "NEWS_CONTEXT": 0.5,
    "EDUCATION_CONTEXT": 0.5,
    "COUNTERSPEECH_CONTEXT": 0.5,
}

# P1-1: minimum context confidence required for the threshold policy
# to fully demote a non-CHILD_SAFETY non-SAFE verdict to SAFE. Below
# this floor the policy emits a 'warn with context' verdict instead,
# preserving the warning surface in the UI while still letting the
# context downgrade severity.
CONTEXT_DEMOTION_CONFIDENCE_THRESHOLD: float = 0.5


# WARNING (P1-1): the overlay-derived context hints below are a
# *coarse heuristic*. Treating a substring match on the community
# overlay id as proof of protected-speech context is fragile:
#
# * It can be spoofed by users who pick a community overlay whose id
#   contains "news" or "education" purely to gain demotion benefit.
# * It cannot tell apart news *coverage of* an atrocity from
#   *celebration of* the same atrocity — only the encoder can.
# * It cannot tell apart counterspeech from the original speech it
#   counters.
#
# Production deployments MUST NOT rely on this signal alone for
# severity demotion. The threshold policy reads the
# ``context_confidence`` carried with each hint and demotes only
# when the confidence is high enough; below that floor the policy
# emits 'warn with context' rather than SAFE.
def derive_context_hints(
    *,
    message: dict[str, Any],
    context: dict[str, Any],
) -> list[str]:
    """Pipeline step 2 (auxiliary): protected-speech context hints.

    Returns a list of hints (subset of NEWS_CONTEXT, EDUCATION_CONTEXT,
    COUNTERSPEECH_CONTEXT, QUOTED_SPEECH_CONTEXT) inferred from the
    message + context envelope. The classifier forwards these into
    ``output.reason_codes``; the threshold policy uses them to demote
    non-CHILD_SAFETY non-SAFE verdicts back to SAFE — protecting news
    coverage, education, and counterspeech from false positives.

    Quoted-speech context is always derived from
    ``message.quoted_from_user``. News / education / counterspeech are
    inferred from ``context.community_overlay_id`` substrings.

    See :func:`derive_context_hints_with_confidence` for the structured
    form that carries a per-hint ``context_confidence`` score.
    """
    return [h["reason_code"] for h in derive_context_hints_with_confidence(
        message=message, context=context
    )]


def derive_context_hints_with_confidence(
    *,
    message: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """P1-1: same as :func:`derive_context_hints` but per-hint confidence.

    Each entry is ``{"reason_code": str, "context_confidence": float}``.
    The threshold policy reads ``context_confidence`` and only demotes
    a non-CHILD_SAFETY non-SAFE verdict to SAFE when the supporting
    confidence is at or above
    :data:`CONTEXT_DEMOTION_CONFIDENCE_THRESHOLD`. Lower-confidence
    hints downgrade to 'warn with context' instead.
    """
    overlay_id = (context.get("community_overlay_id") or "").lower()
    raw: list[str] = []

    if any(tok in overlay_id for tok in _NEWS_CONTEXT_OVERLAY_TOKENS):
        raw.append("NEWS_CONTEXT")
    if any(tok in overlay_id for tok in _EDUCATION_CONTEXT_OVERLAY_TOKENS):
        raw.append("EDUCATION_CONTEXT")
    if any(tok in overlay_id for tok in _COUNTERSPEECH_CONTEXT_OVERLAY_TOKENS):
        raw.append("COUNTERSPEECH_CONTEXT")
    if bool(message.get("quoted_from_user", False)):
        raw.append("QUOTED_SPEECH_CONTEXT")

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for h in raw:
        if h in seen:
            continue
        seen.add(h)
        out.append(
            {
                "reason_code": h,
                "context_confidence": float(
                    CONTEXT_HINT_CONFIDENCE.get(h, 0.3)
                ),
            }
        )
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

    The pipeline supplies the ``constraints`` block (output_format
    json, schema_id pinned). ``temperature`` and ``max_output_tokens``
    are kept for backwards compatibility with older skill packs but
    are ignored by encoder-classifier backends like XLM-R.
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
# ---------------------------------------------------------------------------
# P1-5 — Detector registry.
#
# The current pipeline hard-wires its detectors. ``DetectorRegistry``
# is a thin extensibility seam that lets a deployment plug in
# production-grade detector implementations while keeping the
# reference detectors above as a fallback. The registry is
# intentionally minimal:
#
# * No automatic loading from disk / network — callers register
#   detectors explicitly so the surface stays auditable.
# * No precedence rules between detectors of the same kind — the
#   registry stores a single detector per kind, but the *registered*
#   detector can compose multiple sub-detectors however it likes.
# * Detector callables share the simple ``(text) -> result`` shape
#   already used by the reference detectors, so production detectors
#   can wrap or replace the reference implementations without
#   changing call sites.
# ---------------------------------------------------------------------------
DetectorKind = str  # "url_risk" | "pii" | "scam" | "lexicon" | ...


_REFERENCE_DETECTORS: dict[DetectorKind, Any] = {
    "url_risk": score_url_risk,
    "pii": detect_pii,
    "scam": detect_scam,
    "lexicon": match_lexicons,
    "context_hints": derive_context_hints_with_confidence,
    "homoglyph_map": DEFAULT_HOMOGLYPH_MAP,
}


@dataclass
class DetectorRegistry:
    """Pluggable registry of deterministic detectors.

    A deployment uses :meth:`register` to install production detector
    implementations and :meth:`get` to look one up at pipeline-build
    time. Unregistered kinds resolve to the reference detector defined
    in this module.

    Built-in defaults:

    * ``url_risk`` → :func:`score_url_risk`
    * ``pii``     → :func:`detect_pii`
    * ``scam``    → :func:`detect_scam`
    * ``lexicon`` → :func:`match_lexicons`
    * ``context_hints`` → :func:`derive_context_hints_with_confidence`
    * ``homoglyph_map`` → :data:`DEFAULT_HOMOGLYPH_MAP`

    Production detectors plug in via ``registry.register(kind,
    detector_fn)`` and :class:`GuardrailPipeline` calls them through
    ``self.detector_registry.get(kind)`` during step 2 (deterministic
    detectors) and step 1 (normalization homoglyph map).
    """

    _detectors: dict[DetectorKind, Any] = field(default_factory=dict)

    def register(self, kind: DetectorKind, detector: Any) -> None:
        """Register a detector for ``kind``, replacing any existing one."""
        self._detectors[kind] = detector

    def get(self, kind: DetectorKind) -> Any:
        """Return the detector for ``kind`` (registered or reference)."""
        if kind in self._detectors:
            return self._detectors[kind]
        return _REFERENCE_DETECTORS.get(kind)

    def kinds(self) -> list[DetectorKind]:
        """Return the union of registered and reference detector kinds."""
        return sorted({*self._detectors, *_REFERENCE_DETECTORS})


@dataclass
class GuardrailPipeline:
    """The 7-step hybrid local pipeline.

    Parameters
    ----------
    skill_bundle
        Compiled active skill bundle (global baseline + optional
        jurisdiction overlay + optional community overlay).
    encoder_adapter
        Any :class:`EncoderAdapter` implementation — the real encoder
        classifier (e.g. ``XLMRAdapter``) or
        :class:`MockEncoderAdapter` in tests.
    threshold_policy
        Hard-coded threshold enforcer. Defaults to a fresh
        :class:`ThresholdPolicy`.
    counter_store
        Optional device-local expiring-counter store. When provided,
        step 7 applies ``counter_updates`` from the output.
    detector_registry
        Pluggable :class:`DetectorRegistry` for deterministic
        detectors. Defaults to an empty registry, in which case
        :meth:`classify` falls back to the reference detectors
        defined in this module (``score_url_risk``, ``detect_pii``,
        ``detect_scam``, ``match_lexicons``,
        ``derive_context_hints_with_confidence``). A deployment can
        plug in production detectors by constructing the pipeline
        with a populated registry — see :class:`DetectorRegistry`.
    """

    skill_bundle: SkillBundle
    encoder_adapter: EncoderAdapter
    threshold_policy: ThresholdPolicy = field(default_factory=ThresholdPolicy)
    counter_store: Optional[CounterStore] = None
    detector_registry: DetectorRegistry = field(
        default_factory=DetectorRegistry
    )

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

        **Consumer contract — degraded mode.** When the encoder cannot
        run (model file missing, dependency import failed, inference
        threw, etc.) this method still returns a schema-valid output
        dict, but the output is tagged with
        ``model_health != "healthy"`` and the verdict is built from
        the deterministic detectors alone. In that mode the
        deterministic detectors are the ONLY harm signal, so reason
        codes such as ``PRIVATE_DATA_PATTERN`` / ``SCAM_PATTERN`` /
        ``URL_RISK`` / ``LEXICON_HIT`` may appear on a ``category=0``
        (SAFE) verdict — the threshold policy does not promote SAFE
        outputs to a harm category, so ``severity`` / ``actions`` will
        not reflect those reason codes. Downstream UI consumers
        **MUST** inspect ``model_health`` on every output and surface
        the reason codes to the user when it is anything other than
        ``"healthy"``. A consumer that only branches on
        ``severity`` / ``actions`` will silently miss real harm in
        degraded mode. See the ``model_health`` description in
        ``kchat-skills/global/output_schema.json`` for the full
        contract.
        """
        # --- Step 1: Normalize text.
        text = message.get("text", "") or ""
        norm = self.skill_bundle.normalization or {}
        normalized = normalize_text(
            text,
            nfkc=bool(norm.get("nfkc", True)),
            case_fold=bool(norm.get("case_fold", True)),
        )

        # --- Step 3: Signal packaging (assemble message + context first
        # so context hints can be derived in step 2 below).
        packed_message = {
            "text": text,  # encoder receives *original* text, not normalized.
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

        # --- Step 2: Deterministic local detectors + context hints.
        # P1-5: route through the detector registry so production
        # deployments can swap reference detectors for their own
        # implementations without touching this method. An empty
        # registry resolves every kind back to the reference
        # detector defined in this module — see
        # :class:`DetectorRegistry`.
        registry = self.detector_registry
        url_risk_fn = registry.get("url_risk") or score_url_risk
        pii_fn = registry.get("pii") or detect_pii
        scam_fn = registry.get("scam") or detect_scam
        lexicon_fn = registry.get("lexicon") or match_lexicons
        context_hints_fn = (
            registry.get("context_hints")
            or derive_context_hints_with_confidence
        )
        hints_with_conf = context_hints_fn(
            message=packed_message,
            context=packed_context,
        )
        local_signals = {
            "url_risk": url_risk_fn(normalized),
            "pii_patterns_hit": pii_fn(normalized),
            "scam_patterns_hit": scam_fn(normalized),
            "lexicon_hits": lexicon_fn(
                normalized, self.skill_bundle.lexicons
            ),
            "media_descriptors": extract_media_descriptors(
                message.get("media_descriptors")
            ),
            "context_hints": [h["reason_code"] for h in hints_with_conf],
            "context_hint_confidences": {
                h["reason_code"]: h["context_confidence"]
                for h in hints_with_conf
            },
        }

        packed = pack_signals(
            message=packed_message,
            context=packed_context,
            local_signals=local_signals,
        )

        # --- Step 4: Encoder-based contextual classification
        # (XLM-R reference backend; any EncoderAdapter works).
        #
        # P0-2: if the adapter exposes a non-healthy ``health_state``
        # or the returned output is tagged ``model_health !=
        # "healthy"``, the deterministic detectors from step 2 stay
        # active. The pipeline does not invent a confident SAFE
        # verdict when the encoder cannot run — it propagates the
        # degraded-mode signal so the UI distinguishes 'safe message'
        # from 'model could not run'.
        raw_output = self.encoder_adapter.classify(packed)
        adapter_health = getattr(
            self.encoder_adapter, "health_state", None
        )
        if isinstance(adapter_health, str) and adapter_health not in {
            "",
            "healthy",
        }:
            # The encoder degraded — stamp the *coarser* schema-level
            # value, preserving any existing ``model_health`` that the
            # adapter itself set.
            if not isinstance(raw_output, dict):
                raw_output = {}
            raw_output.setdefault(
                "model_health",
                _ADAPTER_HEALTH_TO_OUTPUT.get(
                    adapter_health, "model_unavailable"
                ),
            )
            raw_output["rationale_id"] = raw_output.get(
                "rationale_id"
            ) or "model_unavailable_rule_only_v1"
            # Surface deterministic-detector signals as reason codes
            # even when the encoder cannot run. ``threshold_policy``
            # is still responsible for setting severity / actions.
            rc = list(raw_output.get("reason_codes") or [])
            if local_signals["pii_patterns_hit"]:
                rc.append("PRIVATE_DATA_PATTERN")
            if local_signals["scam_patterns_hit"]:
                rc.append("SCAM_PATTERN")
            if local_signals["url_risk"] >= 0.5:
                rc.append("URL_RISK")
            if local_signals["lexicon_hits"]:
                rc.append("LEXICON_HIT")
            # De-duplicate preserving order.
            seen: set[str] = set()
            cleaned: list[str] = []
            for code in rc:
                if isinstance(code, str) and code and code not in seen:
                    seen.add(code)
                    cleaned.append(code)
            raw_output["reason_codes"] = cleaned

        # P1-1: forward context hint confidences to the policy step
        # so demotion can be conditional.
        if isinstance(raw_output, dict):
            raw_output.setdefault(
                "context_hint_confidences",
                dict(local_signals["context_hint_confidences"]),
            )

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


# P0-2: mapping from richer adapter-level ``health_state`` values to
# the coarser ``model_health`` enum exposed on the output schema.
#
# Canonical table lives on :mod:`encoder_adapter` so the pipeline and
# every backend project internal states onto the output schema the
# same way — see ``HEALTH_TO_MODEL_HEALTH_OUTPUT`` there. This
# module-level alias is kept for readability at the call site.
_ADAPTER_HEALTH_TO_OUTPUT: dict[str, str] = HEALTH_TO_MODEL_HEALTH_OUTPUT


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
    # Internal threshold-policy hint; never leaves the pipeline.
    output.pop("context_hint_confidences", None)
    return output


__all__ = [
    "CONTEXT_DEMOTION_CONFIDENCE_THRESHOLD",
    "CONTEXT_HINT_CONFIDENCE",
    "DEFAULT_HOMOGLYPH_MAP",
    "DetectorKind",
    "DetectorRegistry",
    "GuardrailPipeline",
    "LexiconEntry",
    "SkillBundle",
    "derive_context_hints",
    "derive_context_hints_with_confidence",
    "detect_pii",
    "detect_scam",
    "extract_media_descriptors",
    "match_lexicons",
    "normalize_text",
    "pack_signals",
    "score_url_risk",
]
