"""Shared fixtures for kchat-skills/communities tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMMUNITIES_DIR = Path(__file__).resolve().parents[2] / "communities"

COMMUNITY_FILES = (
    "school.yaml",
    "family.yaml",
    "workplace.yaml",
    "adult_only.yaml",
    "marketplace.yaml",
    "health_support.yaml",
    "political.yaml",
    "gaming.yaml",
)


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def communities_dir() -> Path:
    return COMMUNITIES_DIR


@pytest.fixture(scope="session")
def community_overlays(communities_dir: Path) -> dict[str, dict]:
    """Return all 8 community overlays as a dict keyed by file name."""
    return {name: _load(communities_dir / name) for name in COMMUNITY_FILES}


@pytest.fixture(scope="session")
def community_template(communities_dir: Path) -> dict:
    return _load(communities_dir / "_template" / "overlay.yaml")
