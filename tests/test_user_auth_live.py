"""Live validation of the user-account (app/web) login (#40/#41).

Confirms the reverse-engineered protocol against the real iSolarCloud API — chiefly that
the shipped RSA public key and ``sys_code`` are the ones the server currently accepts, so
a login actually succeeds end-to-end. Read-only.

Run with:  pytest -m live
Requires SUNGROW_USER_ACCOUNT / SUNGROW_USER_PASSWORD (and optionally SUNGROW_HOST).
"""

import pytest

from pysolarcloud import UserAuth


@pytest.mark.live
async def test_user_login_and_list_plants(live_user_credentials):
    """A real user account can log in and list its plants (validates key/sys_code/envelope)."""
    async with UserAuth(
        live_user_credentials["host"],
        live_user_credentials["email"],
        live_user_credentials["password"],
    ) as auth:
        # Login is exercised implicitly by the first authenticated read; a wrong RSA key
        # or sys_code surfaces here as a PySolarCloudException with the API's result_msg.
        plants = await auth.async_get_plants()
        assert auth.token, "login did not yield a token"
        assert isinstance(plants, list)
        for plant in plants:
            assert "ps_id" in plant
