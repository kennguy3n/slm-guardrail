"""Contract tests for the regulatory-alignment documentation.

Spec reference: PHASES.md Phase 6, "Regulatory alignment". The three
documents under ``kchat-skills/docs/regulatory/`` are load-bearing
review artefacts; this test pins the following invariants:

* Each of the three alignment documents exists and is non-empty.
* Each document references the core source artefacts it claims to map
  (e.g. ``baseline.yaml``, ``anti_misuse.py``).
* The ``README.md`` index links to all three alignment documents.
"""
from __future__ import annotations

from pathlib import Path

import pytest


DOCS_DIR = (
    Path(__file__).resolve().parents[2] / "docs" / "regulatory"
)

EU_DSA = DOCS_DIR / "eu_dsa_alignment.md"
NIST_AI_RMF = DOCS_DIR / "nist_ai_rmf_alignment.md"
UNICEF_ITU_COP = DOCS_DIR / "unicef_itu_cop_alignment.md"
INDEX = DOCS_DIR / "README.md"


ALL_DOCS = (EU_DSA, NIST_AI_RMF, UNICEF_ITU_COP, INDEX)


# Minimum document length: each alignment doc should be a substantial
# mapping, not a stub.
MIN_CHARS = 2000


@pytest.mark.parametrize(
    "doc",
    ALL_DOCS,
    ids=lambda p: p.name,
)
def test_doc_exists_and_non_empty(doc: Path):
    assert doc.exists(), f"missing regulatory doc: {doc}"
    text = doc.read_text(encoding="utf-8")
    assert text.strip(), f"regulatory doc empty: {doc}"
    assert len(text) >= MIN_CHARS, (
        f"{doc} must be at least {MIN_CHARS} characters; got {len(text)}"
    )


def test_eu_dsa_references_core_artefacts():
    text = EU_DSA.read_text(encoding="utf-8")
    for needle in (
        "baseline.yaml",
        "anti_misuse.py",
        "appeal_flow.py",
        "skill_passport.py",
        "DSA",
    ):
        assert needle in text, f"eu_dsa_alignment.md must reference {needle!r}"


def test_nist_ai_rmf_references_core_artefacts():
    text = NIST_AI_RMF.read_text(encoding="utf-8")
    for needle in (
        "baseline.yaml",
        "anti_misuse.py",
        "bias_audit.py",
        "metric_validator.py",
        "benchmark.py",
        "appeal_flow.py",
        "Govern",
        "Map",
        "Measure",
        "Manage",
    ):
        assert needle in text, f"nist_ai_rmf_alignment.md must reference {needle!r}"


def test_unicef_itu_references_core_artefacts_and_all_59_countries():
    text = UNICEF_ITU_COP.read_text(encoding="utf-8")
    for needle in (
        "baseline.yaml",
        "anti_misuse.py",
        "appeal_flow.py",
        "child_safety_policy",
        "severity_floor: 5",
    ):
        assert needle in text, (
            f"unicef_itu_cop_alignment.md must reference {needle!r}"
        )
    # All 59 country ISO codes should appear in the per-jurisdiction table.
    all_59 = (
        # Phase 5 wave 1.
        "US", "DE", "BR", "IN", "JP",
        # Phase 5 wave 2.
        "MX", "CA", "AR", "CO", "CL", "PE",
        "FR", "GB", "ES", "IT", "NL", "PL", "SE", "PT", "CH", "AT",
        "KR", "ID", "PH", "TH", "VN", "MY", "SG", "TW", "PK", "BD",
        "NG", "ZA", "EG", "SA", "AE", "KE",
        "AU", "NZ", "TR",
        # Phase 6 expansion (19 additional countries).
        "RU", "UA", "RO", "GR", "CZ", "HU",
        "DK", "FI", "NO",
        "IE",
        "IL", "IQ",
        "MA", "DZ",
        "GH", "TZ", "ET",
        "EC", "UY",
    )
    assert len(all_59) == 59
    for cc in all_59:
        # Each ISO code should appear as a table row entry.
        needle = f"| {cc} |"
        assert needle in text, (
            f"unicef_itu_cop_alignment.md missing per-jurisdiction row for {cc}"
        )


def test_index_links_to_all_three_alignment_docs():
    text = INDEX.read_text(encoding="utf-8")
    for target in (
        "eu_dsa_alignment.md",
        "nist_ai_rmf_alignment.md",
        "unicef_itu_cop_alignment.md",
    ):
        assert target in text, f"regulatory README.md must link to {target!r}"


def test_all_alignment_docs_cite_baseline_yaml():
    for doc in (EU_DSA, NIST_AI_RMF, UNICEF_ITU_COP):
        text = doc.read_text(encoding="utf-8")
        assert "baseline.yaml" in text, (
            f"{doc.name} must cite baseline.yaml as the single source of truth"
        )


def test_docs_do_not_contain_raw_harm_strings():
    # Privacy-contract sanity: regulatory docs describe the system, not
    # harm payloads. Explicit slur placeholders are allowed
    # (``<SLUR_TOKEN_A>``, ``<CSAM_INDICATOR_TOKEN>``); literal harm
    # strings must not be present.
    forbidden_fragments = (
        "kill you",  # example violence trigger (literal)
        "verify your account now",
    )
    for doc in ALL_DOCS:
        text = doc.read_text(encoding="utf-8").lower()
        for frag in forbidden_fragments:
            assert frag not in text, (
                f"{doc.name}: regulatory doc must not embed literal harm "
                f"strings (found {frag!r})"
            )
