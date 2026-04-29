"""Tests for ``kchat-skills/prompts/compiled_examples/`` reference outputs.

Each compiled example must:

* Exist on disk and be non-empty.
* Contain every required ``[SECTION]`` marker.
* Stay strictly under the 1800 instruction-token budget per
  ``compiled_prompt_format.md``.
* Reproduce byte-for-byte from the live :class:`SkillPackCompiler` —
  i.e. the file on disk equals what the compiler would emit for that
  combination right now (catches stale references).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from compiler import (  # type: ignore[import-not-found]
    MAX_INSTRUCTION_TOKENS,
    SkillPackCompiler,
    estimate_tokens,
    parse_compiled_sections,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "kchat-skills" / "prompts" / "compiled_examples"


# Reference combinations captured under compiled_examples/.
# Tuple format: (filename_stem, jurisdiction or None, community or None).
EXAMPLES: tuple[tuple[str, str | None, str | None], ...] = (
    ("baseline_only", None, None),
    ("community_school", None, "school"),
    ("community_family", None, "family"),
    ("community_workplace", None, "workplace"),
    ("community_adult_only", None, "adult_only"),
    ("community_marketplace", None, "marketplace"),
    ("community_health_support", None, "health_support"),
    ("community_political", None, "political"),
    ("community_gaming", None, "gaming"),
    ("jurisdiction_strict_adult", "archetype-strict-adult", None),
    ("jurisdiction_strict_hate", "archetype-strict-hate", None),
    (
        "jurisdiction_strict_marketplace",
        "archetype-strict-marketplace",
        None,
    ),
    (
        "strict_marketplace_workplace",
        "archetype-strict-marketplace",
        "workplace",
    ),
    ("strict_adult_school", "archetype-strict-adult", "school"),
    # Phase 5 first-wave country packs.
    ("country_us", "us", None),
    ("country_de", "de", None),
    ("country_br", "br", None),
    ("country_in", "in", None),
    ("country_jp", "jp", None),
    # Phase 5 second-wave country packs (35 additional countries).
    # Americas.
    ("country_mx", "mx", None),
    ("country_ca", "ca", None),
    ("country_ar", "ar", None),
    ("country_co", "co", None),
    ("country_cl", "cl", None),
    ("country_pe", "pe", None),
    # Europe.
    ("country_fr", "fr", None),
    ("country_gb", "gb", None),
    ("country_es", "es", None),
    ("country_it", "it", None),
    ("country_nl", "nl", None),
    ("country_pl", "pl", None),
    ("country_se", "se", None),
    ("country_pt", "pt", None),
    ("country_ch", "ch", None),
    ("country_at", "at", None),
    # Asia-Pacific.
    ("country_kr", "kr", None),
    ("country_id", "id", None),
    ("country_ph", "ph", None),
    ("country_th", "th", None),
    ("country_vn", "vn", None),
    ("country_my", "my", None),
    ("country_sg", "sg", None),
    ("country_tw", "tw", None),
    ("country_pk", "pk", None),
    ("country_bd", "bd", None),
    # Middle East & Africa.
    ("country_ng", "ng", None),
    ("country_za", "za", None),
    ("country_eg", "eg", None),
    ("country_sa", "sa", None),
    ("country_ae", "ae", None),
    ("country_ke", "ke", None),
    # Other.
    ("country_au", "au", None),
    ("country_nz", "nz", None),
    ("country_tr", "tr", None),
    # Phase 6 expansion country packs (19 additional countries).
    # Eastern Europe.
    ("country_ru", "ru", None),
    ("country_ua", "ua", None),
    ("country_ro", "ro", None),
    ("country_gr", "gr", None),
    ("country_cz", "cz", None),
    ("country_hu", "hu", None),
    # Nordics.
    ("country_dk", "dk", None),
    ("country_fi", "fi", None),
    ("country_no", "no", None),
    # Western Europe / Atlantic.
    ("country_ie", "ie", None),
    # Middle East.
    ("country_il", "il", None),
    ("country_iq", "iq", None),
    # North Africa.
    ("country_ma", "ma", None),
    ("country_dz", "dz", None),
    # Sub-Saharan Africa.
    ("country_gh", "gh", None),
    ("country_tz", "tz", None),
    ("country_et", "et", None),
    # Latin America.
    ("country_ec", "ec", None),
    ("country_uy", "uy", None),
)


REQUIRED_SECTIONS = (
    "[INSTRUCTION]",
    "[GLOBAL_BASELINE]",
    "[JURISDICTION_OVERLAY]",
    "[COMMUNITY_OVERLAY]",
    "[INPUT]",
    "[OUTPUT]",
)


@pytest.mark.parametrize("stem,jurisdiction,community", EXAMPLES)
class TestCompiledExamples:
    def test_file_exists_and_non_empty(self, stem, jurisdiction, community):
        path = EXAMPLES_DIR / f"{stem}.txt"
        assert path.exists(), f"{path} missing"
        text = path.read_text(encoding="utf-8")
        assert text.strip(), f"{path} is empty"

    def test_required_sections_present(self, stem, jurisdiction, community):
        path = EXAMPLES_DIR / f"{stem}.txt"
        text = path.read_text(encoding="utf-8")
        for marker in REQUIRED_SECTIONS:
            assert marker in text, f"{path} missing section {marker}"

    def test_under_instruction_budget(self, stem, jurisdiction, community):
        path = EXAMPLES_DIR / f"{stem}.txt"
        text = path.read_text(encoding="utf-8")
        sections = parse_compiled_sections(text)
        # Sum the four instruction-budget sections only.
        instruction_text = "\n".join(
            "[" + name + "]\n" + sections[name]
            for name in (
                "INSTRUCTION",
                "GLOBAL_BASELINE",
                "JURISDICTION_OVERLAY",
                "COMMUNITY_OVERLAY",
            )
        )
        tokens = estimate_tokens(instruction_text)
        assert tokens < MAX_INSTRUCTION_TOKENS, (
            f"{path} instruction tokens {tokens} >= {MAX_INSTRUCTION_TOKENS}"
        )

    def test_matches_live_compiler_output(self, stem, jurisdiction, community):
        compiler = SkillPackCompiler(repo_root=REPO_ROOT)
        live = compiler.compile(jurisdiction=jurisdiction, community=community)
        on_disk = (EXAMPLES_DIR / f"{stem}.txt").read_text(encoding="utf-8")
        assert on_disk == live.text, (
            f"{stem}.txt is stale relative to the live compiler — "
            "regenerate via tools/regenerate_compiled_examples.py."
        )


# ---------------------------------------------------------------------------
# Coverage assertions across the example set.
# ---------------------------------------------------------------------------
class TestExamplesCoverage:
    def test_baseline_only_present(self):
        assert (EXAMPLES_DIR / "baseline_only.txt").exists()

    def test_all_eight_communities_covered(self):
        community_stems = {
            "community_school",
            "community_family",
            "community_workplace",
            "community_adult_only",
            "community_marketplace",
            "community_health_support",
            "community_political",
            "community_gaming",
        }
        for stem in community_stems:
            assert (EXAMPLES_DIR / f"{stem}.txt").exists(), stem

    def test_total_compiled_example_count_is_73(self):
        """Phase 6 target: 73 compiled examples total.

        14 Phase 1-4 references (baseline + 8 communities + 3 archetypes
        + 2 combos) + 40 Phase 5 country packs + 19 Phase 6 country
        packs = 73.
        """
        assert len(EXAMPLES) == 73, (
            f"expected 73 reference compiled examples; got {len(EXAMPLES)}"
        )
        on_disk = sorted(p.name for p in EXAMPLES_DIR.glob("*.txt"))
        assert len(on_disk) == 73, (
            f"expected 73 reference .txt files in {EXAMPLES_DIR}; "
            f"got {len(on_disk)}"
        )

    def test_all_three_jurisdiction_archetypes_covered(self):
        for stem in (
            "jurisdiction_strict_adult",
            "jurisdiction_strict_hate",
            "jurisdiction_strict_marketplace",
        ):
            assert (EXAMPLES_DIR / f"{stem}.txt").exists(), stem

    def test_at_least_two_combination_examples(self):
        # The two representative jurisdiction+community combos.
        for stem in (
            "strict_marketplace_workplace",
            "strict_adult_school",
        ):
            assert (EXAMPLES_DIR / f"{stem}.txt").exists(), stem

    def test_phase5_first_wave_country_packs_covered(self):
        for stem in (
            "country_us",
            "country_de",
            "country_br",
            "country_in",
            "country_jp",
        ):
            assert (EXAMPLES_DIR / f"{stem}.txt").exists(), stem

    def test_phase5_all_40_country_packs_covered(self):
        country_codes = (
            # Wave 1.
            "us", "de", "br", "in", "jp",
            # Wave 2 — Americas.
            "mx", "ca", "ar", "co", "cl", "pe",
            # Wave 2 — Europe.
            "fr", "gb", "es", "it", "nl", "pl", "se", "pt", "ch", "at",
            # Wave 2 — Asia-Pacific.
            "kr", "id", "ph", "th", "vn", "my", "sg", "tw", "pk", "bd",
            # Wave 2 — Middle East & Africa.
            "ng", "za", "eg", "sa", "ae", "ke",
            # Wave 2 — Other.
            "au", "nz", "tr",
        )
        assert len(country_codes) == 40, (
            "Phase 5 delivers exactly 40 country packs; country_codes tuple "
            "must remain in sync with tools/regenerate_compiled_examples.py."
        )
        for cc in country_codes:
            path = EXAMPLES_DIR / f"country_{cc}.txt"
            assert path.exists(), (
                f"Phase 5 country pack {cc!r} missing compiled example {path}"
            )

    def test_phase6_all_19_country_packs_covered(self):
        country_codes = (
            # Eastern Europe.
            "ru", "ua", "ro", "gr", "cz", "hu",
            # Nordics.
            "dk", "fi", "no",
            # Western Europe / Atlantic.
            "ie",
            # Middle East.
            "il", "iq",
            # North Africa.
            "ma", "dz",
            # Sub-Saharan Africa.
            "gh", "tz", "et",
            # Latin America.
            "ec", "uy",
        )
        assert len(country_codes) == 19, (
            "Phase 6 delivers exactly 19 additional country packs; "
            "country_codes tuple must remain in sync with "
            "tools/regenerate_compiled_examples.py."
        )
        for cc in country_codes:
            path = EXAMPLES_DIR / f"country_{cc}.txt"
            assert path.exists(), (
                f"Phase 6 country pack {cc!r} missing compiled example {path}"
            )
