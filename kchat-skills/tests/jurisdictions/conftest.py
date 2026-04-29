"""Shared fixtures for kchat-skills/jurisdictions tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


JURISDICTIONS_DIR = Path(__file__).resolve().parents[2] / "jurisdictions"


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def jurisdictions_dir() -> Path:
    return JURISDICTIONS_DIR


@pytest.fixture(scope="session")
def jurisdiction_template(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "_template" / "overlay.yaml")


@pytest.fixture(scope="session")
def strict_adult_overlay(jurisdictions_dir: Path) -> dict:
    return _load(
        jurisdictions_dir / "archetype-strict-adult" / "overlay.yaml"
    )


@pytest.fixture(scope="session")
def strict_adult_normalization(jurisdictions_dir: Path) -> dict:
    return _load(
        jurisdictions_dir / "archetype-strict-adult" / "normalization.yaml"
    )


@pytest.fixture(scope="session")
def strict_hate_overlay(jurisdictions_dir: Path) -> dict:
    return _load(
        jurisdictions_dir / "archetype-strict-hate" / "overlay.yaml"
    )


@pytest.fixture(scope="session")
def strict_hate_normalization(jurisdictions_dir: Path) -> dict:
    return _load(
        jurisdictions_dir / "archetype-strict-hate" / "normalization.yaml"
    )
