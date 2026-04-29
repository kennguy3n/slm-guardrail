"""Shared fixtures for kchat-skills/global tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

GLOBAL_DIR = Path(__file__).resolve().parents[2] / "global"


@pytest.fixture(scope="session")
def global_dir() -> Path:
    return GLOBAL_DIR


@pytest.fixture(scope="session")
def taxonomy(global_dir: Path) -> dict:
    with (global_dir / "taxonomy.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def severity(global_dir: Path) -> dict:
    with (global_dir / "severity.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def output_schema(global_dir: Path) -> dict:
    with (global_dir / "output_schema.json").open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def baseline(global_dir: Path) -> dict:
    with (global_dir / "baseline.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def local_signal_schema(global_dir: Path) -> dict:
    with (global_dir / "local_signal_schema.json").open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def privacy_contract(global_dir: Path) -> dict:
    with (global_dir / "privacy_contract.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def prompts_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "prompts"
