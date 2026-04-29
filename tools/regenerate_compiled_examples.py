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
