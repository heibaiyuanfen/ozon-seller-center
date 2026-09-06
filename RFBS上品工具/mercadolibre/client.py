"""Read-only Global Selling API access for the isolated Mercado Libre module.

This phase deliberately has no token exchange, item submission, or other HTTP
write operations. Account credentials are supplied in memory by the caller.
"""

from __future__ import annotations

import math
import re
from urllib.parse import urlencode, urlsplit

import requests


API_BASE_URL = "https://api.mercadolibre.com"
AUTHORIZATION_URL = "https://global-selling.mercadolibre.com/authorization"

# Never reflect an arbitrary server message/code or a requests exception: both
# can contain the bearer token. Unknown remote codes use a local generic code.
_KNOWN_ERROR_CODES = frozenset({
    "bad_request", "unauthorized", "forbidden", "not_found", "conflict",
    "method_not_allowed", "unsupported_media_type", "too_many_requests",
    "internal_server_error", "service_unavailable", "invalid_access_token",
    "invalid_token", "validation_error", "invalid_user_id", "invalid_category_id",
})


class MercadoLibreAPIError(RuntimeError):
    """A sanitized failure with HTTP status (when available) and a safe code."""

    def __init__(self, status: int | None, code: str):
        self.status = status
        self.code = code
        status_label = str(status) if status is not None else "unavailable"
        super().__init__(f"Mercado Libre API failed (status={status_label}, code={code})")


def _nonblank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonblank string")
    return value.strip()


def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str = "",
) -> str:
    """Build the Global Selling authorization URL without opening a browser.

    The caller must generate/store a fresh state and validate it on callback.
    If PKCE is used, the caller must retain the corresponding code verifier.
    """
    client_id = _nonblank(client_id, "client_id")
    redirect_uri = _nonblank(redirect_uri, "redirect_uri")
    _nonblank(state, "state")
    if any(ord(character) < 32 or ord(character) == 127 for character in redirect_uri):
        raise ValueError("redirect_uri must be an absolute HTTPS URL")
    try:
        parsed = urlsplit(redirect_uri)
        valid_redirect = (
            parsed.scheme == "https" and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
            and not parsed.fragment
        )
        # Accessing port also rejects malformed/non-numeric/out-of-range ports.
        parsed.port
    except ValueError:
        valid_redirect = False
    if not valid_redirect:
        raise ValueError("redirect_uri must be an absolute HTTPS URL without credentials or fragment")
    parameters = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if not isinstance(code_challenge, str):
        raise ValueError("code_challenge must be a string")
    if code_challenge:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", code_challenge):
            raise ValueError("code_challenge must be a 43-128 character base64url PKCE challenge")
        parameters["code_challenge"] = code_challenge
        parameters["code_challenge_method"] = "S256"
    return AUTHORIZATION_URL + "?" + urlencode(parameters)


class MercadoLibreReadClient:
    """GET-only client for account capabilities and the CBT category schema."""

    def __init__(self, access_token: str, session=None, timeout: float = 20):
        token = _nonblank(access_token, "access_token")
        if any(ord(character) < 32 or ord(character) == 127 for character in token):
            raise ValueError("access_token must not contain control characters")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a finite positive number")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        self._access_token = token
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout

    def me(self) -> dict:
        return self._get("/users/me", dict)

    def marketplaces(self, user_id: str | int) -> dict:
        if isinstance(user_id, bool) or not isinstance(user_id, (str, int)):
            raise ValueError("user_id must be a positive numeric identifier")
        identifier = str(user_id).strip()
        if not re.fullmatch(r"[1-9][0-9]*", identifier):
            raise ValueError("user_id must be a positive numeric identifier")
        return self._get(f"/marketplace/users/{identifier}", dict)

    def category(self, category_id: str) -> dict:
        return self._get(f"/categories/{self._category_id(category_id)}", dict)

    def category_attributes(self, category_id: str) -> list[dict]:
        return self._get(f"/categories/{self._category_id(category_id)}/attributes", list)

    def categories(self) -> list[dict]:
        return self._get("/sites/CBT/categories", list)

    @staticmethod
    def _category_id(category_id: str) -> str:
        identifier = _nonblank(category_id, "category_id")
        if not re.fullmatch(r"CBT[0-9]+", identifier):
            raise ValueError("category_id must be a CBT category identifier")
        return identifier

    def _get(self, path: str, expected_type: type):
        try:
            response = self._session.get(
                API_BASE_URL + path,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise MercadoLibreAPIError(None, "network_error") from None
        status = response.status_code
        if isinstance(status, bool) or not isinstance(status, int):
            raise MercadoLibreAPIError(None, "invalid_response")
        if 300 <= status < 400:
            raise MercadoLibreAPIError(status, "redirect_not_allowed")
        if not 200 <= status < 300:
            code = "http_error"
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = None
            if isinstance(error_payload, dict):
                candidate = error_payload.get("error")
                if isinstance(candidate, str) and candidate in _KNOWN_ERROR_CODES:
                    if self._access_token not in candidate:
                        code = candidate
            raise MercadoLibreAPIError(status, code)
        try:
            payload = response.json()
        except ValueError:
            raise MercadoLibreAPIError(status, "invalid_json") from None
        if not isinstance(payload, expected_type):
            raise MercadoLibreAPIError(status, "invalid_response")
        if expected_type is list and not all(isinstance(item, dict) for item in payload):
            raise MercadoLibreAPIError(status, "invalid_response")
        return payload
