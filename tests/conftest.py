"""Shared pytest fixtures for pysolarcloud tests."""

import os

import pytest

# Load a local .env (walks up to the workspace root) so live tests can pick up
# credentials during local runs. In CI the values come from repository secrets, so a
# missing python-dotenv is non-fatal.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a dev-only convenience
    pass


@pytest.fixture
def live_user_credentials():
    """Return live user-account credentials for the app/web login, or skip.

    Live validation of ``UserAuth`` needs a real iSolarCloud user account, supplied via
    ``SUNGROW_USER_ACCOUNT`` / ``SUNGROW_USER_PASSWORD`` (and optionally ``SUNGROW_HOST``
    for the region). Absent those, the test skips so ordinary CI and local runs are
    unaffected.
    """
    email = os.getenv("SUNGROW_USER_ACCOUNT")
    password = os.getenv("SUNGROW_USER_PASSWORD")
    if not email or not password:
        pytest.skip("SUNGROW_USER_ACCOUNT / SUNGROW_USER_PASSWORD not set; skipping live user-login test")
    return {
        "email": email,
        "password": password,
        "host": os.getenv("SUNGROW_HOST", "https://gateway.isolarcloud.eu"),
    }
