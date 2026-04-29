"""Regenerate every reference compiled prompt under
``kchat-skills/prompts/compiled_examples/``.

Run from the repo root::

    python tools/regenerate_compiled_examples.py

The set of (jurisdiction, community) combinations covered here is
mirrored by ``kchat-skills/tests/global/test_compiled_examples.py``;
the test fails if the on-disk file diverges from what the compiler
produces today.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "kchat-skills" / "compiler"))

from compiler import SkillPackCompiler  # noqa: E402  (import after sys.path)


COMBOS: tuple[tuple[str, str | None, str | None], ...] = (
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
)


def main() -> int:
    compiler = SkillPackCompiler(repo_root=REPO_ROOT)
    out_dir = REPO_ROOT / "kchat-skills" / "prompts" / "compiled_examples"
    out_dir.mkdir(parents=True, exist_ok=True)

    for stem, jurisdiction, community in COMBOS:
        prompt = compiler.compile(
            jurisdiction=jurisdiction, community=community
        )
        path = out_dir / f"{stem}.txt"
        path.write_text(prompt.text, encoding="utf-8")
        print(f"  {stem:40s} {prompt.instruction_tokens:4d} tokens -> {path}")

    print(f"Wrote {len(COMBOS)} compiled examples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
