"""Shared test fixtures for beads-tui."""
import json
import sys
from pathlib import Path

import pytest

# Make the project root importable so `import beads_tui` works under pytest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def list_sample_json() -> str:
    return (FIXTURES / "list_sample.json").read_text()


@pytest.fixture
def comments_sample_json() -> str:
    return (FIXTURES / "comments_sample.json").read_text()


@pytest.fixture
def comments_empty_json() -> str:
    return (FIXTURES / "comments_empty.json").read_text()


@pytest.fixture
def list_sample(list_sample_json) -> list:
    return json.loads(list_sample_json)
