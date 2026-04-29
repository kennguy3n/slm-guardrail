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
