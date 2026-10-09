import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import ReferenceProduct, scrape_reference


class ScrapeTimeoutTests(unittest.TestCase):
    def test_network_failures_use_browser_for_same_product_and_profile(self):
        for error in [requests.ReadTimeout("30 seconds"), requests.ConnectTimeout("connection"), requests.ConnectionError("proxy disconnected")]:
            with self.subTest(error=type(error).__name__):
                session = Mock()
                session.get.side_effect = error
                reference = ReferenceProduct("https://www.ozon.ru/product/3988655096/", "current product")
                log = Mock()
                with patch("core.scrape_reference_browser", return_value=reference) as browser:
                    result = scrape_reference("3988655096", session=session, profile_dir="isolated-profile", log_func=log)
                self.assertIs(result, reference)
                browser.assert_called_once_with(reference.source_url, timeout=120, profile_dir="isolated-profile", log_func=log)
                log.assert_called_once()

    def test_prefetch_keeps_browser_disabled_and_propagates_error(self):
        session = Mock()
        session.get.side_effect = requests.ReadTimeout("timeout")
        with patch("core.scrape_reference_browser") as browser:
            with self.assertRaises(requests.ReadTimeout):
                scrape_reference("3988655096", session=session, browser_fallback=False)
            browser.assert_not_called()

    def test_browser_failure_is_reported_without_fabricating_product(self):
        session = Mock()
        session.get.side_effect = requests.ReadTimeout("timeout")
        with patch("core.scrape_reference_browser", side_effect=RuntimeError("browser could not read current product")):
            with self.assertRaisesRegex(RuntimeError, "current product"):
                scrape_reference("3988655096", session=session)

if __name__ == "__main__":
    unittest.main()
