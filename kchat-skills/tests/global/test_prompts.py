"""Validation tests for the runtime SLM instruction prompt and the
compiled-prompt format reference + example.

See ARCHITECTURE.md "Runtime SLM Instruction Prompt" (lines 595-619) and
"Compiled Prompt Example" (lines 621-660).
"""
from __future__ import annotations

import re

import pytest


COMPILED_REQUIRED_SECTIONS = [
    "[INSTRUCTION]",
    "[GLOBAL_BASELINE]",
    "[JURISDICTION_OVERLAY]",
    "[COMMUNITY_OVERLAY]",
    "[INPUT]",
    "[OUTPUT]",
]

# ARCHITECTURE.md compiled-prompt instruction budget.
INSTRUCTION_TOKEN_BUDGET = 1800


def _read(path) -> str:
    with path.open("r", encoding="utf-8") as f:
        return f.read()


def test_runtime_instruction_exists_and_non_empty(prompts_dir):
    path = prompts_dir / "runtime_instruction.txt"
    assert path.is_file(), f"missing {path}"
    text = _read(path).strip()
    assert text, "runtime_instruction.txt must not be empty"


def test_runtime_instruction_contains_ten_numbered_rules(prompts_dir):
    text = _read(prompts_dir / "runtime_instruction.txt")
    # Match "1.", "2.", ... "10." each at the start of a line (allowing
    # leading whitespace for indented continuation lines we ignore).
    found = []
    for n in range(1, 11):
        pattern = rf"(?m)^\s*{n}\."
        assert re.search(pattern, text), f"missing rule number {n}."
        found.append(n)
    assert found == list(range(1, 11))


@pytest.mark.parametrize(
    "phrase",
    [
        "on-device safety assistant",
        "JSON schema kchat.guardrail.output.v1",
        "CHILD_SAFETY",
        "severity floor of 5",
        "0.45",
    ],
)
def test_runtime_instruction_contains_key_phrase(prompts_dir, phrase):
    text = _read(prompts_dir / "runtime_instruction.txt")
    assert phrase in text, f"runtime_instruction.txt missing required phrase: {phrase!r}"


def test_runtime_instruction_fits_token_budget(prompts_dir):
    """Approximate token count via word count / 0.75; must be well under 1800."""
    text = _read(prompts_dir / "runtime_instruction.txt")
    word_count = len(text.split())
    approx_tokens = word_count / 0.75
    assert approx_tokens < INSTRUCTION_TOKEN_BUDGET, (
        f"instruction approx_tokens={approx_tokens:.0f} exceeds budget "
        f"{INSTRUCTION_TOKEN_BUDGET}"
    )


def test_compiled_prompt_format_doc_exists(prompts_dir):
    path = prompts_dir / "compiled_prompt_format.md"
    assert path.is_file(), f"missing {path}"
    text = _read(path)
    for section in COMPILED_REQUIRED_SECTIONS:
        assert section in text, f"compiled_prompt_format.md missing section: {section}"


def test_compiled_example_exists_with_required_sections(prompts_dir):
    path = prompts_dir / "compiled_examples" / "workplace_strict_marketplace.txt"
    assert path.is_file(), f"missing {path}"
    text = _read(path)
    for section in COMPILED_REQUIRED_SECTIONS:
        assert section in text, (
            f"workplace_strict_marketplace.txt missing section: {section}"
        )


def test_compiled_example_section_order(prompts_dir):
    """Sections must appear in the documented order."""
    path = prompts_dir / "compiled_examples" / "workplace_strict_marketplace.txt"
    text = _read(path)
    positions = [text.find(s) for s in COMPILED_REQUIRED_SECTIONS]
    assert all(p >= 0 for p in positions)
    assert positions == sorted(positions), (
        f"compiled example section order is wrong: {positions}"
    )
