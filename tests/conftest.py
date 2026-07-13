import os

import pytest


@pytest.fixture(scope="session")
def test_database_url():
    value = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value

