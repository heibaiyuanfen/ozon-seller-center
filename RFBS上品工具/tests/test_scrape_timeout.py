import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import ReferenceProduct, scrape_reference


class ScrapeTimeoutTests(unittest.TestCase):
    def test_shared_browser_profile_serializes_threads(self):
        from browser_runtime import browser_profile_lock
        from concurrent.futures import ThreadPoolExecutor
        import tempfile
        import time
        active = []
        peak = []
        with tempfile.TemporaryDirectory() as directory:
            def collect(_):
                with browser_profile_lock(Path(directory)):
                    active.append(1)
                    peak.append(len(active))
                    time.sleep(0.03)
                    active.pop()
            with ThreadPoolExecutor(max_workers=3) as workers:
                list(workers.map(collect, range(3)))
        self.assertEqual(max(peak), 1)

    def test_empty_gallery_browser_failure_does_not_open_second_window(self):
        session = Mock()
        session.get.return_value.status_code = 200
        session.get.return_value.url = "https://www.ozon.ru/product/3988655096/"
        reference = ReferenceProduct(session.get.return_value.url, "current product")
        with patch("core.parse_reference_html", return_value=reference), patch(
            "core.scrape_reference_browser", side_effect=RuntimeError("人工验证超时")
        ) as browser:
            with self.assertRaisesRegex(RuntimeError, "人工验证超时"):
                scrape_reference("3988655096", session=session)
        browser.assert_called_once()

    def test_navigation_interruption_retries_same_page(self):
        from browser_runtime import evaluate_after_navigation
        import time
        page = Mock()
        page.is_closed.return_value = False
        page.evaluate.side_effect = [RuntimeError("Execution context was destroyed, most likely because of a navigation"), {"title": "product"}]
        self.assertEqual(evaluate_after_navigation(page, "script", time.monotonic() + 10), {"title": "product"})
        self.assertEqual(page.evaluate.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(500)

    def test_non_navigation_error_is_not_swallowed(self):
        from browser_runtime import evaluate_after_navigation
        import time
        page = Mock()
        page.evaluate.side_effect = RuntimeError("invalid script")
        with self.assertRaisesRegex(RuntimeError, "invalid script"):
            evaluate_after_navigation(page, "script", time.monotonic() + 10)
        page.wait_for_timeout.assert_not_called()

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
