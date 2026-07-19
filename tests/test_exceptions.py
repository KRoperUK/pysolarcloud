"""Tests for typed AuthError / RateLimitError classification (issue #23).

Documented iSolarCloud ``result_code`` values are mapped to typed subclasses of
``PySolarCloudException`` at raise time so consumers can branch reauth-vs-retry by
``isinstance`` instead of maintaining a code list. ``PySolarCloudException`` stays the
common parent, so existing ``except PySolarCloudException`` / ``.error`` checks keep
working.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientResponse

from pysolarcloud import AuthError, PySolarCloudException, RateLimitError
from pysolarcloud.plants import Plants

# Documented codes → expected class (Appendix 2 / Appendix 9 result codes).
AUTH_CODES = ["E00003", "E900", "E919", "E912", "E914"]
RATE_LIMIT_CODES = ["E998", "E999"]


def _mock_response(json_data: dict, *, status: int = 200) -> ClientResponse:
    response = MagicMock(spec=ClientResponse)
    response.status = status
    response.raise_for_status = MagicMock()
    response.json = AsyncMock(return_value=json_data)
    return response


@pytest.fixture
def auth():
    auth = MagicMock()
    auth.lang = "_en_US"
    auth.request = AsyncMock()
    return auth


@pytest.fixture
def plants(auth):
    return Plants(auth)


# --------------------------------------------------------------------------- #
# Direct classifier unit tests (PySolarCloudException.from_response)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", AUTH_CODES)
def test_from_response_maps_auth_codes(code):
    """Each documented auth code becomes an AuthError (and a PySolarCloudException)."""
    exc = PySolarCloudException.from_response({"result_code": code, "result_msg": "dead creds"})
    assert isinstance(exc, AuthError)
    assert isinstance(exc, PySolarCloudException)
    assert not isinstance(exc, RateLimitError)
    assert exc.error == code
    assert exc.result_msg == "dead creds"


@pytest.mark.parametrize("code", RATE_LIMIT_CODES)
def test_from_response_maps_rate_limit_codes(code):
    """Quota/throttle codes become a RateLimitError, NOT an AuthError (they are transient)."""
    exc = PySolarCloudException.from_response({"result_code": code, "result_msg": "slow down"})
    assert isinstance(exc, RateLimitError)
    assert isinstance(exc, PySolarCloudException)
    assert not isinstance(exc, AuthError)
    assert exc.error == code


def test_from_response_unknown_code_is_base_exception():
    """An unmapped code stays the base PySolarCloudException, not a typed subclass."""
    exc = PySolarCloudException.from_response({"result_code": "E001", "result_msg": "generic"})
    assert type(exc) is PySolarCloudException
    assert not isinstance(exc, AuthError)
    assert not isinstance(exc, RateLimitError)
    assert exc.error == "E001"


def test_from_response_honours_legacy_error_key():
    """The legacy ``{"error": ...}`` envelope is classified the same way as result_code."""
    exc = PySolarCloudException.from_response({"error": "E00003"})
    assert isinstance(exc, AuthError)
    assert exc.error == "E00003"


def test_from_response_plain_string_is_base_exception():
    """A bare string message (no code) yields the base exception."""
    exc = PySolarCloudException.from_response("boom")
    assert type(exc) is PySolarCloudException
    assert exc.error == "boom"


@pytest.mark.parametrize("code", AUTH_CODES + RATE_LIMIT_CODES + ["E001"])
def test_from_response_all_caught_by_base(code):
    """Backward compat: every classified exception is caught by ``except PySolarCloudException``."""
    with pytest.raises(PySolarCloudException):
        raise PySolarCloudException.from_response({"result_code": code})


# --------------------------------------------------------------------------- #
# Business-method path (proves the raise site is wired, not just the classifier)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", AUTH_CODES)
@pytest.mark.asyncio
async def test_business_method_raises_auth_error(auth, plants, code):
    """A business method that hits an auth result_code raises the typed AuthError."""
    auth.request.return_value = _mock_response({"result_code": code, "result_msg": "auth failed"})
    with pytest.raises(AuthError) as exc:
        await plants.async_get_plants()
    assert isinstance(exc.value, PySolarCloudException)
    assert exc.value.error == code


@pytest.mark.parametrize("code", RATE_LIMIT_CODES)
@pytest.mark.asyncio
async def test_business_method_raises_rate_limit_error(auth, plants, code):
    """A business method that hits a quota/throttle result_code raises RateLimitError."""
    auth.request.return_value = _mock_response({"result_code": code, "result_msg": "throttled"})
    with pytest.raises(RateLimitError) as exc:
        await plants.async_get_plants()
    assert isinstance(exc.value, PySolarCloudException)
    assert not isinstance(exc.value, AuthError)
    assert exc.value.error == code


@pytest.mark.asyncio
async def test_business_method_unknown_code_is_base(auth, plants):
    """An unmapped error code surfaces as the base exception (not a typed subclass)."""
    auth.request.return_value = _mock_response({"result_code": "E001", "result_msg": "generic"})
    with pytest.raises(PySolarCloudException) as exc:
        await plants.async_get_plants()
    assert type(exc.value) is PySolarCloudException
    assert not isinstance(exc.value, (AuthError, RateLimitError))
    assert exc.value.error == "E001"


@pytest.mark.asyncio
async def test_business_method_auth_error_caught_by_base(auth, plants):
    """Backward compat: existing ``except PySolarCloudException`` still catches auth errors."""
    auth.request.return_value = _mock_response({"result_code": "E00003", "result_msg": "auth failed"})
    with pytest.raises(PySolarCloudException):
        await plants.async_get_plants()


# --------------------------------------------------------------------------- #
# RateLimitError.retry_after (#61)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("retry_after", 60, 60.0),
        ("retry_after", "60", 60.0),
        ("retry_after", 60.5, 60.5),
        ("retryAfter", 30, 30.0),
        ("retry_in", 45, 45.0),
        ("retry_seconds", 90, 90.0),
    ],
)
def test_rate_limit_error_exposes_retry_after(key, raw, expected):
    """Server-supplied retry hints (in any observed spelling) are exposed as seconds."""
    exc = PySolarCloudException.from_response({"result_code": "E999", key: raw})
    assert isinstance(exc, RateLimitError)
    assert exc.retry_after == expected


@pytest.mark.parametrize("raw", [None, "not-a-number", -1, 0, "", "nope"])
def test_rate_limit_error_retry_after_none_on_bogus_values(raw):
    """Unparseable or non-positive retry hints are dropped rather than propagated."""
    exc = PySolarCloudException.from_response({"result_code": "E998", "retry_after": raw})
    assert isinstance(exc, RateLimitError)
    assert exc.retry_after is None


def test_rate_limit_error_absent_retry_after_defaults_to_none():
    """A rate-limit response without any retry hint leaves ``retry_after`` as None."""
    exc = PySolarCloudException.from_response({"result_code": "E999", "result_msg": "throttled"})
    assert isinstance(exc, RateLimitError)
    assert exc.retry_after is None


def test_rate_limit_error_direct_string_construction_has_no_retry_after():
    """A ``RateLimitError`` built from a bare string keeps ``.retry_after`` at ``None``."""
    exc = RateLimitError("throttled")
    assert exc.retry_after is None
    assert exc.error == "throttled"


# --------------------------------------------------------------------------- #
# DeviceNotWritableError (#63)
# --------------------------------------------------------------------------- #


def test_device_not_writable_error_is_pysolarcloudexception():
    """``DeviceNotWritableError`` is a ``PySolarCloudException`` subclass with a typed error code."""
    from pysolarcloud import DeviceNotWritableError

    raw = {"result_code": "1", "result_data": {"dev_result_list": [{"code": "9"}]}}
    exc = DeviceNotWritableError(raw, device_code="9")

    assert isinstance(exc, PySolarCloudException)
    assert exc.error == "device_not_writable"
    assert exc.device_code == "9"
    assert exc.response is raw


def test_device_not_writable_error_description_includes_device_code():
    """The human-readable description carries the raw device code when supplied."""
    from pysolarcloud import DeviceNotWritableError

    exc = DeviceNotWritableError({}, device_code="9")
    assert "9" in str(exc)


def test_device_not_writable_error_without_device_code():
    """Constructing without a device_code still produces a sensible description."""
    from pysolarcloud import DeviceNotWritableError

    exc = DeviceNotWritableError({})
    assert exc.device_code is None
    assert str(exc)  # non-empty
