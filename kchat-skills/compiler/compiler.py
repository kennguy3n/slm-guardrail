"""Skill-pack compiler — Phase 4 of the KChat Guardrail roadmap.

Pipeline (ARCHITECTURE.md "Skill Pack Compiler Pipeline" lines 666-679):

    1. Load YAML skill pack (global baseline + optional jurisdiction
       overlay + optional community overlay).
    2. Validate against anti-misuse rules (see :mod:`anti_misuse`).
    3. Generate / load test suite from skill rules.
    4. Resolve the active skill bundle and compile a single compact
       compiled prompt within the 1800 instruction-token budget.
    5. Run test suite + collect metrics (see :mod:`metric_validator`).
    6. If metrics pass, produce a signed bundle (see :mod:`skill_passport`).

This module owns steps 1, 4, and 5; signing (step 6) is delegated to
:mod:`skill_passport` and pack validation (step 2) to :mod:`anti_misuse`.

Conflict resolution follows the global baseline's
``skill_selection.conflict_resolution`` block:

* ``severity``: take_max
* ``category``: most_specific_overlay (community > jurisdiction > baseline)
* ``action``: most_protective
* ``privacy_rules``: immutable (any pack that weakens the 8 baseline
  rules is rejected)
* ``child_safety``: floor 5 (CHILD_SAFETY pinned to severity 5
  regardless of overlay configuration)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


# Token budgets — mirror compiled_prompt_format.md.
MAX_INSTRUCTION_TOKENS = 1800
MAX_OUTPUT_TOKENS = 600

# Action protectiveness ranking — higher index = more protective.
_ACTION_RANK: dict[str, int] = {
    "label_only": 1,
    "warn": 2,
    "strong_warn": 3,
    "block": 4,
    "critical_intervention": 5,
}

# Severity floor → action mapping (severity-rubric-aligned default).
_SEVERITY_DEFAULT_ACTION: dict[int, str] = {
    0: "label_only",
    1: "label_only",
    2: "label_only",
    3: "warn",
    4: "strong_warn",
    5: "critical_intervention",
}

CHILD_SAFETY_CATEGORY = 1


# ---------------------------------------------------------------------------
# Token approximation.
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """Conservative token-count estimate for the compiled prompt.

    Uses ``ceil(len(text) / 4)`` — the common Anthropic/OpenAI rule of
    thumb that one English token is ~4 characters. This deliberately
    over-counts compared with a real tokenizer so packs that pass our
    budget cleanly stay safely below the 1800-token cap on every
    target classifier backend (XLM-R MiniLM-L6, plus any future
    encoder or generative-classifier adapter) without re-running the
    tokenizer.
    """
    if not text:
        return 0
    n_chars = len(text)
    return -(-n_chars // 4)  # ceil division


# ---------------------------------------------------------------------------
# Resolved active skill bundle (post conflict-resolution).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CategoryRule:
    """Per-category resolved rule combining baseline + overlays."""

    category: int
    severity_floor: int
    action: str
    source: str  # "baseline" / "jurisdiction" / "community"


@dataclass
class ActiveSkillBundle:
    """Resolved bundle = global baseline + jurisdiction + community.

    Construct via :func:`resolve_active_bundle`. The compiler converts
    one of these into a compiled prompt string via :func:`compile_prompt`.
    """

    baseline: dict[str, Any]
    jurisdiction: Optional[dict[str, Any]] = None
    community: Optional[dict[str, Any]] = None
    category_rules: dict[int, CategoryRule] = field(default_factory=dict)
    allowed_contexts: list[str] = field(default_factory=list)
    counters: list[dict[str, Any]] = field(default_factory=list)
    runtime_context: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pack loading.
# ---------------------------------------------------------------------------
def load_pack(path: Path | str) -> dict[str, Any]:
    """Read a YAML skill pack from disk and return the raw mapping."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Skill pack {p} did not parse to a mapping")
    return data


# ---------------------------------------------------------------------------
# Privacy-rule immutability check.
# ---------------------------------------------------------------------------
class PrivacyRuleViolation(ValueError):
    """Raised when an overlay attempts to redefine baseline privacy rules."""


def _baseline_privacy_rule_ids(baseline: dict[str, Any]) -> set[int]:
    rules = (baseline.get("privacy_rules") or {}).get("rules") or []
    return {int(r["id"]) for r in rules if "id" in r}


def assert_privacy_rules_intact(
    baseline: dict[str, Any], overlay: dict[str, Any]
) -> None:
    """Reject any overlay that contains a ``privacy_rules`` block.

    Privacy rules are sourced exclusively from the global baseline.
    Overlays may *not* redefine, remove, or extend them — even adding
    a new rule that looks more restrictive is a vector for sneaking
    bad behaviour into a signed pack.
    """
    if "privacy_rules" not in overlay:
        return
    raise PrivacyRuleViolation(
        f"overlay '{overlay.get('skill_id', '<unknown>')}' attempts to "
        "redefine privacy_rules; rules are immutable from baseline"
    )


# ---------------------------------------------------------------------------
# Conflict resolution.
# ---------------------------------------------------------------------------
def _action_for_severity(severity: int) -> str:
    return _SEVERITY_DEFAULT_ACTION.get(int(severity), "label_only")


def _most_protective(actions: Iterable[str]) -> str:
    best = "label_only"
    best_rank = _ACTION_RANK[best]
    for a in actions:
        rank = _ACTION_RANK.get(a, 0)
        if rank > best_rank:
            best = a
            best_rank = rank
    return best


def resolve_active_bundle(
    *,
    baseline: dict[str, Any],
    jurisdiction: Optional[dict[str, Any]] = None,
    community: Optional[dict[str, Any]] = None,
    runtime_context: Optional[dict[str, Any]] = None,
) -> ActiveSkillBundle:
    """Resolve ``baseline + jurisdiction + community`` into a bundle.

    Conflict resolution per ``baseline.skill_selection.conflict_resolution``:

    * ``severity_floor``: take_max across all layers.
    * ``action``: most_protective across all layers.
    * ``category``: community > jurisdiction > baseline.
    * ``privacy_rules``: immutable — overlays may not redefine.
    * ``child_safety``: pin severity 5 on category 1 regardless of overlays.
    """
    for overlay in (jurisdiction, community):
        if overlay is not None:
            assert_privacy_rules_intact(baseline, overlay)

    rules: dict[int, CategoryRule] = {}

    # Start with baseline child_safety floor on category 1.
    rules[CHILD_SAFETY_CATEGORY] = CategoryRule(
        category=CHILD_SAFETY_CATEGORY,
        severity_floor=5,
        action="critical_intervention",
        source="baseline",
    )

    # Fold jurisdiction overrides — most layered first; community last.
    for overlay, source in (
        (jurisdiction, "jurisdiction"),
        (community, "community"),
    ):
        if overlay is None:
            continue
        # Jurisdiction overlays use ``overrides`` with ``severity_floor``.
        for ov in overlay.get("overrides") or []:
            cat = int(ov["category"])
            sev = int(ov.get("severity_floor", 0))
            action = _action_for_severity(sev)
            existing = rules.get(cat)
            new_sev = max(existing.severity_floor, sev) if existing else sev
            new_action = (
                _most_protective([existing.action, action])
                if existing
                else action
            )
            rules[cat] = CategoryRule(
                category=cat,
                severity_floor=new_sev,
                action=new_action,
                source=source if not existing else existing.source,
            )
        # Community overlays use ``rules`` with ``action`` (no severity_floor).
        for r in overlay.get("rules") or []:
            cat = int(r["category"])
            action = str(r.get("action", "warn"))
            existing = rules.get(cat)
            # Most-specific-overlay-wins for *category-level metadata*; we
            # still take_max severity because the severity floor of an
            # under-layer must hold regardless of community choice.
            sev_floor = existing.severity_floor if existing else 0
            new_action = (
                _most_protective([existing.action, action])
                if existing
                else action
            )
            rules[cat] = CategoryRule(
                category=cat,
                severity_floor=sev_floor,
                action=new_action,
                source=source,
            )

    # Pin CHILD_SAFETY at severity 5 / critical_intervention regardless.
    rules[CHILD_SAFETY_CATEGORY] = CategoryRule(
        category=CHILD_SAFETY_CATEGORY,
        severity_floor=5,
        action="critical_intervention",
        source="baseline",
    )

    # allowed_contexts — union of all layers, preserve order seen.
    allowed: list[str] = []
    for layer in (baseline, jurisdiction, community):
        if layer is None:
            continue
        for ctx in layer.get("allowed_contexts") or []:
            if ctx not in allowed:
                allowed.append(ctx)

    counters: list[dict[str, Any]] = []
    if community is not None:
        for c in community.get("group_risk_counters") or []:
            counters.append(c)

    return ActiveSkillBundle(
        baseline=baseline,
        jurisdiction=jurisdiction,
        community=community,
        category_rules=rules,
        allowed_contexts=allowed,
        counters=counters,
        runtime_context=runtime_context or {},
    )


# ---------------------------------------------------------------------------
# Compiled-prompt generation.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CompiledPrompt:
    """A complete compiled prompt + telemetry."""

    text: str
    instruction_tokens: int
    sections: dict[str, str]

    def __str__(self) -> str:
        return self.text


_INSTRUCTION_CACHE: dict[Path, str] = {}


def _load_runtime_instruction(prompts_dir: Path) -> str:
    p = prompts_dir / "runtime_instruction.txt"
    if p in _INSTRUCTION_CACHE:
        return _INSTRUCTION_CACHE[p]
    text = p.read_text(encoding="utf-8").rstrip() + "\n"
    _INSTRUCTION_CACHE[p] = text
    return text


_TAXONOMY_NAME_CACHE: dict[Path, dict[int, str]] = {}


def _load_taxonomy_names(global_dir: Path) -> dict[int, str]:
    p = global_dir / "taxonomy.yaml"
    if p in _TAXONOMY_NAME_CACHE:
        return _TAXONOMY_NAME_CACHE[p]
    with p.open("r", encoding="utf-8") as f:
        tax = yaml.safe_load(f)
    names = {int(c["id"]): str(c["name"]) for c in tax.get("categories", [])}
    _TAXONOMY_NAME_CACHE[p] = names
    return names


def _section_global_baseline() -> str:
    return (
        "taxonomy: 16-category v1\n"
        "severity: 0..5 v1\n"
        "privacy_rules: v1 (immutable)\n"
        "output_schema: kchat.guardrail.output.v1\n"
        "thresholds: label_only=0.45 warn=0.62 strong_warn=0.78 critical=0.85\n"
    )


def _wrap_allowed_contexts(contexts: list[str], indent: int = 18) -> str:
    """Format ``allowed_contexts`` as a wrapped, comma-separated list."""
    if not contexts:
        return ""
    line = ", ".join(contexts)
    # Hard-wrap at ~60 chars to mimic the existing reference example.
    width = 60
    out_lines: list[str] = []
    current = ""
    for token in contexts:
        candidate = (current + ", " + token) if current else token
        if len(candidate) > width and current:
            out_lines.append(current + ",")
            current = token
        else:
            current = candidate
    if current:
        out_lines.append(current)
    pad = " " * indent
    return ("\n" + pad).join(out_lines)


def _section_jurisdiction(
    bundle: ActiveSkillBundle, taxonomy_names: dict[int, str]
) -> str:
    j = bundle.jurisdiction
    if not j:
        return ""
    lines = [f"id: {j.get('skill_id', '<unknown>')}"]
    lines.append("overrides:")
    for ov in j.get("overrides") or []:
        cat = int(ov["category"])
        name = taxonomy_names.get(cat, f"CAT_{cat}")
        sev = int(ov.get("severity_floor", 0))
        lines.append(f"  - category {cat} {name} severity_floor {sev}")
    if bundle.allowed_contexts:
        wrapped = _wrap_allowed_contexts(bundle.allowed_contexts)
        lines.append(f"allowed_contexts: {wrapped}")
    return "\n".join(lines) + "\n"


def _section_community(
    bundle: ActiveSkillBundle, taxonomy_names: dict[int, str]
) -> str:
    c = bundle.community
    if not c:
        return ""
    lines = [f"id: {c.get('skill_id', '<unknown>')}"]
    profile = c.get("community_profile") or {}
    if profile.get("age_mode"):
        lines.append(f"age_mode: {profile['age_mode']}")
    rules = c.get("rules") or []
    if rules:
        lines.append("rules:")
        for r in rules:
            cat = int(r["category"])
            name = taxonomy_names.get(cat, f"CAT_{cat}")
            action = r.get("action", "warn")
            extra: list[str] = []
            for k in ("suggest_redact", "suggest_mute"):
                if r.get(k):
                    extra.append(f"{k}=true")
            line = f"  - category {cat} {name} action={action}"
            if extra:
                line += " " + " ".join(extra)
            lines.append(line)
    counters = c.get("group_risk_counters") or []
    if counters:
        lines.append("counters:")
        for ctr in counters:
            cid = ctr.get("counter_id", "<counter>")
            t = ctr.get("thresholds") or {}
            label = t.get("label_at", "?")
            strong = t.get("strong_label_at", t.get("escalate_at", "?"))
            esc = t.get("escalate_at", "?")
            lines.append(f"  - {cid} thresholds {label}/{strong}/{esc}")
    return "\n".join(lines) + "\n"


def compile_prompt(
    bundle: ActiveSkillBundle,
    *,
    prompts_dir: Path,
    global_dir: Path,
    input_placeholder: str = "<structured input contract instance>",
    output_placeholder: str = "<JSON conforming to kchat.guardrail.output.v1>",
    max_instruction_tokens: int = MAX_INSTRUCTION_TOKENS,
) -> CompiledPrompt:
    """Compile ``bundle`` into a single compact prompt string.

    Format mirrors :file:`prompts/compiled_prompt_format.md` with six
    sections: ``[INSTRUCTION]``, ``[GLOBAL_BASELINE]``,
    ``[JURISDICTION_OVERLAY]``, ``[COMMUNITY_OVERLAY]``, ``[INPUT]``,
    ``[OUTPUT]``. The first four form the "instruction" budget that
    must fit under ``max_instruction_tokens``.
    """
    instruction = _load_runtime_instruction(prompts_dir)
    taxonomy_names = _load_taxonomy_names(global_dir)

    global_section = _section_global_baseline()
    jurisdiction_section = _section_jurisdiction(bundle, taxonomy_names)
    community_section = _section_community(bundle, taxonomy_names)

    parts: list[str] = []
    parts.append("[INSTRUCTION]\n" + instruction.rstrip() + "\n")
    parts.append("[GLOBAL_BASELINE]\n" + global_section.rstrip() + "\n")
    parts.append(
        "[JURISDICTION_OVERLAY]"
        + ("\n" + jurisdiction_section.rstrip() if jurisdiction_section else "")
        + "\n"
    )
    parts.append(
        "[COMMUNITY_OVERLAY]"
        + ("\n" + community_section.rstrip() if community_section else "")
        + "\n"
    )

    instruction_text = "\n".join(parts)
    instruction_tokens = estimate_tokens(instruction_text)
    if instruction_tokens > max_instruction_tokens:
        raise InstructionBudgetExceeded(
            f"Compiled instruction exceeds budget: {instruction_tokens} "
            f"> {max_instruction_tokens}"
        )

    full = (
        instruction_text
        + "\n[INPUT]\n"
        + input_placeholder
        + "\n\n[OUTPUT]\n"
        + output_placeholder
        + "\n"
    )

    return CompiledPrompt(
        text=full,
        instruction_tokens=instruction_tokens,
        sections={
            "INSTRUCTION": instruction.rstrip(),
            "GLOBAL_BASELINE": global_section.rstrip(),
            "JURISDICTION_OVERLAY": jurisdiction_section.rstrip(),
            "COMMUNITY_OVERLAY": community_section.rstrip(),
            "INPUT": input_placeholder,
            "OUTPUT": output_placeholder,
        },
    )


class InstructionBudgetExceeded(ValueError):
    """Raised when a compiled prompt overruns ``max_instruction_tokens``."""


# ---------------------------------------------------------------------------
# Helpers used by tests / CLI.
# ---------------------------------------------------------------------------
def parse_compiled_sections(text: str) -> dict[str, str]:
    """Round-trip helper: split a compiled prompt back into sections."""
    expected = (
        "[INSTRUCTION]",
        "[GLOBAL_BASELINE]",
        "[JURISDICTION_OVERLAY]",
        "[COMMUNITY_OVERLAY]",
        "[INPUT]",
        "[OUTPUT]",
    )
    sections: dict[str, str] = {}
    cursor = 0
    for i, marker in enumerate(expected):
        idx = text.find(marker, cursor)
        if idx == -1:
            raise ValueError(f"Compiled prompt missing section {marker}")
        if i > 0:
            prev_name = expected[i - 1].strip("[]")
            sections[prev_name] = text[cursor:idx].rstrip()
        cursor = idx + len(marker)
        cursor = cursor + 1 if cursor < len(text) and text[cursor] == "\n" else cursor
    sections[expected[-1].strip("[]")] = text[cursor:].rstrip()
    return sections


# ---------------------------------------------------------------------------
# Top-level orchestrator.
# ---------------------------------------------------------------------------
@dataclass
class SkillPackCompiler:
    """High-level entry point combining load → resolve → compile.

    ``repo_root`` is the absolute path of the slm-guardrail repo root
    (the directory holding ``kchat-skills``). Tests instantiate the
    compiler with a real path; signed-bundle production happens via
    :func:`SkillPackCompiler.compile`.
    """

    repo_root: Path

    @property
    def kchat_skills_dir(self) -> Path:
        return self.repo_root / "kchat-skills"

    @property
    def global_dir(self) -> Path:
        return self.kchat_skills_dir / "global"

    @property
    def prompts_dir(self) -> Path:
        return self.kchat_skills_dir / "prompts"

    def load_baseline(self) -> dict[str, Any]:
        return load_pack(self.global_dir / "baseline.yaml")

    def load_jurisdiction(self, archetype_or_path: str | Path) -> dict[str, Any]:
        p = Path(archetype_or_path)
        if not p.is_absolute() and not p.exists():
            p = self.kchat_skills_dir / "jurisdictions" / str(archetype_or_path) / "overlay.yaml"
        return load_pack(p)

    def load_community(self, name_or_path: str | Path) -> dict[str, Any]:
        p = Path(name_or_path)
        if not p.is_absolute() and not p.exists():
            cand = self.kchat_skills_dir / "communities" / f"{name_or_path}.yaml"
            if cand.exists():
                p = cand
            else:
                p = self.kchat_skills_dir / "communities" / str(name_or_path)
        return load_pack(p)

    def compile(
        self,
        *,
        jurisdiction: Optional[dict[str, Any] | str | Path] = None,
        community: Optional[dict[str, Any] | str | Path] = None,
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> CompiledPrompt:
        baseline = self.load_baseline()

        j_pack = (
            jurisdiction
            if isinstance(jurisdiction, dict) or jurisdiction is None
            else self.load_jurisdiction(jurisdiction)
        )
        c_pack = (
            community
            if isinstance(community, dict) or community is None
            else self.load_community(community)
        )

        bundle = resolve_active_bundle(
            baseline=baseline,
            jurisdiction=j_pack,
            community=c_pack,
            runtime_context=runtime_context,
        )
        return compile_prompt(
            bundle,
            prompts_dir=self.prompts_dir,
            global_dir=self.global_dir,
        )


__all__ = [
    "ActiveSkillBundle",
    "CHILD_SAFETY_CATEGORY",
    "CategoryRule",
    "CompiledPrompt",
    "InstructionBudgetExceeded",
    "MAX_INSTRUCTION_TOKENS",
    "MAX_OUTPUT_TOKENS",
    "PrivacyRuleViolation",
    "SkillPackCompiler",
    "assert_privacy_rules_intact",
    "compile_prompt",
    "estimate_tokens",
    "load_pack",
    "parse_compiled_sections",
    "resolve_active_bundle",
]


def _main(argv: list[str]) -> int:
    """Command-line entrypoint: ``python -m compiler.compiler <pack.yaml>``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="kchat-skill-compiler",
        description="Compile a KChat guardrail skill pack to a prompt bundle.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="Path to the slm-guardrail repo root (defaults to autodetect).",
    )
    parser.add_argument(
        "--jurisdiction",
        default=None,
        help="Jurisdiction overlay name (e.g. archetype-strict-marketplace).",
    )
    parser.add_argument(
        "--community",
        default=None,
        help="Community overlay name (e.g. workplace).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional file path to write the compiled prompt to.",
    )
    args = parser.parse_args(argv)

    compiler = SkillPackCompiler(repo_root=Path(args.repo_root))
    prompt = compiler.compile(
        jurisdiction=args.jurisdiction,
        community=args.community,
    )
    if args.out:
        Path(args.out).write_text(prompt.text, encoding="utf-8")
    else:
        print(prompt.text, end="")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
