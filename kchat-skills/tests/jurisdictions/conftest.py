"""Shared fixtures for kchat-skills/jurisdictions tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


JURISDICTIONS_DIR = Path(__file__).resolve().parents[2] / "jurisdictions"
GLOBAL_DIR = Path(__file__).resolve().parents[2] / "global"


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def local_signal_schema() -> dict:
    with (GLOBAL_DIR / "local_signal_schema.json").open(
        "r", encoding="utf-8"
    ) as f:
        return json.load(f)


@pytest.fixture(scope="session")
def output_schema() -> dict:
    with (GLOBAL_DIR / "output_schema.json").open("r", encoding="utf-8") as f:
        return json.load(f)


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


@pytest.fixture(scope="session")
def strict_marketplace_overlay(jurisdictions_dir: Path) -> dict:
    return _load(
        jurisdictions_dir / "archetype-strict-marketplace" / "overlay.yaml"
    )


@pytest.fixture(scope="session")
def strict_marketplace_normalization(jurisdictions_dir: Path) -> dict:
    return _load(
        jurisdictions_dir / "archetype-strict-marketplace" / "normalization.yaml"
    )


# ---------------------------------------------------------------------------
# Country-specific overlay / normalization fixtures (Phase 5 first wave).
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def us_overlay(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "us" / "overlay.yaml")


@pytest.fixture(scope="session")
def us_normalization(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "us" / "normalization.yaml")


@pytest.fixture(scope="session")
def de_overlay(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "de" / "overlay.yaml")


@pytest.fixture(scope="session")
def de_normalization(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "de" / "normalization.yaml")


@pytest.fixture(scope="session")
def br_overlay(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "br" / "overlay.yaml")


@pytest.fixture(scope="session")
def br_normalization(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "br" / "normalization.yaml")


@pytest.fixture(scope="session")
def in_overlay(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "in" / "overlay.yaml")


@pytest.fixture(scope="session")
def in_normalization(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "in" / "normalization.yaml")


@pytest.fixture(scope="session")
def jp_overlay(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "jp" / "overlay.yaml")


@pytest.fixture(scope="session")
def jp_normalization(jurisdictions_dir: Path) -> dict:
    return _load(jurisdictions_dir / "jp" / "normalization.yaml")
