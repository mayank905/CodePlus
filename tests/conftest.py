"""Shared pytest fixtures (`tests/conftest.py`)."""

from __future__ import annotations

import pytest

from db.connection import get_client
from db.seed import seed_all


@pytest.fixture(scope="session")
def db_client():
    """Return a connected FalkorDB client with both tenant graphs freshly seeded."""
    client = get_client()
    seed_all(client=client, reset=True)
    return client
