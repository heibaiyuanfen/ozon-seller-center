import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import requests

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from mercadolibre.client import (
    MercadoLibreAPIError,
    MercadoLibreReadClient,
    build_authorization_url,
)


class MercadoLibreReadClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=["get"])
        self.response = Mock(status_code=200)
        self.session.get.return_value = self.response
        self.client = MercadoLibreReadClient("secret-bearer-token", session=self.session, timeout=7)

    def test_all_public_api_operations_use_fixed_host_get_and_no_redirect(self):
        cases = [
            (self.client.me, (), "/users/me", {"id": 123}),
            (self.client.marketplaces, (123,), "/marketplace/users/123", {"marketplaces": []}),
            (self.client.category, ("CBT123",), "/categories/CBT123", {"id": "CBT123"}),
            (self.client.category_attributes, ("CBT123",), "/categories/CBT123/attributes", [{"id": "BRAND"}]),
            (self.client.categories, (), "/sites/CBT/categories", [{"id": "CBT123"}]),
        ]
        for method, arguments, path, payload in cases:
            with self.subTest(path=path):
                self.response.json.return_value = payload
                self.assertEqual(method(*arguments), payload)
                self.session.get.assert_called_with(
                    "https://api.mercadolibre.com" + path,
                    headers={"Authorization": "Bearer secret-bearer-token", "Accept": "application/json"},
                    timeout=7,
                    allow_redirects=False,
                )

    def test_rejects_path_injection_before_transport(self):
        for identifier in ("../me", "123?secret=1", "123/attributes", "https://evil.example", True, -1, 0, "１２３"):
            with self.subTest(user_id=identifier), self.assertRaises(ValueError):
                self.client.marketplaces(identifier)
        for identifier in ("MLM123", "CBT123/attributes", "CBT123?x=1", "../CBT123", "CBT１２３", None):
            with self.subTest(category_id=identifier), self.assertRaises(ValueError):
                self.client.category(identifier)
        self.session.get.assert_not_called()

    def test_rejects_missing_or_header_injection_token(self):
        for token in (None, "", "  ", "token\r\nInjected: secret", "token\tsecret"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                MercadoLibreReadClient(token, session=self.session)

    def test_rejects_unbounded_timeouts(self):
        for timeout in (0, -1, None, True, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                MercadoLibreReadClient("token", session=self.session, timeout=timeout)

    def test_redirect_is_blocked_without_reading_location_or_following(self):
        self.response.status_code = 302
        self.response.headers = {"Location": "https://evil.example/token"}
        with self.assertRaises(MercadoLibreAPIError) as raised:
            self.client.me()
        self.assertEqual((raised.exception.status, raised.exception.code), (302, "redirect_not_allowed"))
        self.session.get.assert_called_once()
        self.assertFalse(self.session.get.call_args.kwargs["allow_redirects"])
        self.response.json.assert_not_called()

    def test_http_failure_exposes_status_and_known_code_but_not_remote_message(self):
        self.response.status_code = 401
        self.response.json.return_value = {"error": "unauthorized", "message": "Bearer secret-bearer-token"}
        with self.assertRaises(MercadoLibreAPIError) as raised:
            self.client.me()
        self.assertEqual((raised.exception.status, raised.exception.code), (401, "unauthorized"))
        self.assertNotIn("secret-bearer-token", str(raised.exception))

    def test_untrusted_error_code_is_not_reflected(self):
        self.response.status_code = 400
        for code in ("secret-bearer-token", {"token": "secret-bearer-token"}, "custom_untrusted_code"):
            self.response.json.return_value = {"error": code}
            with self.subTest(code=code), self.assertRaises(MercadoLibreAPIError) as raised:
                self.client.me()
            self.assertEqual(raised.exception.code, "http_error")
            self.assertNotIn("secret-bearer-token", str(raised.exception))

    def test_transport_failure_is_sanitized(self):
        self.session.get.side_effect = requests.ConnectionError("request headers: Bearer secret-bearer-token")
        with self.assertRaises(MercadoLibreAPIError) as raised:
            self.client.me()
        self.assertEqual((raised.exception.status, raised.exception.code), (None, "network_error"))
        self.assertNotIn("secret-bearer-token", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def test_response_json_and_endpoint_shapes_are_validated(self):
        cases = [(self.client.me, []), (self.client.categories, {}), (self.client.categories, ["CBT123"])]
        for method, payload in cases:
            self.response.json.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(MercadoLibreAPIError) as raised:
                method()
            self.assertEqual(raised.exception.code, "invalid_response")
        self.response.json.side_effect = ValueError("invalid document containing secret-bearer-token")
        with self.assertRaises(MercadoLibreAPIError) as raised:
            self.client.me()
        self.assertEqual(raised.exception.code, "invalid_json")
        self.assertNotIn("secret-bearer-token", str(raised.exception))


class MercadoLibreAuthorizationURLTests(unittest.TestCase):
    def test_authorization_url_uses_global_selling_and_encodes_callback_and_state(self):
        redirect = "https://seller.example/callback?tenant=one&mode=global"
        result = build_authorization_url("123456", redirect, "random-state+&中文")
        parsed = urlsplit(result)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), (
            "https", "global-selling.mercadolibre.com", "/authorization",
        ))
        self.assertEqual(parse_qs(parsed.query), {
            "response_type": ["code"], "client_id": ["123456"],
            "redirect_uri": [redirect], "state": ["random-state+&中文"],
        })

    def test_pkce_challenge_uses_s256(self):
        challenge = "a" * 43
        query = parse_qs(urlsplit(build_authorization_url("123", "https://seller.example/callback", "state", challenge)).query)
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])

    def test_rejects_unsafe_redirects_empty_state_and_invalid_pkce(self):
        for redirect in ("http://seller.example/callback", "//seller.example", "javascript:alert(1)",
                         "https://user:secret@seller.example", "https://seller.example/#fragment",
                         "https://seller.example:bad/", "https://seller.example/\ncallback"):
            with self.subTest(redirect=redirect), self.assertRaises(ValueError):
                build_authorization_url("123", redirect, "state")
        with self.assertRaises(ValueError):
            build_authorization_url("123", "https://seller.example", "  ")
        with self.assertRaises(ValueError):
            build_authorization_url("123", "https://seller.example", "state", "short")


if __name__ == "__main__":
    unittest.main()
