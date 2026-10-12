import inspect
import re
import unittest

from core import _scrape_reference_browser_locked


class GalleryDomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(channel="msedge", headless=True)
        except Exception as error:
            cls.playwright.stop()
            raise unittest.SkipTest(f"Edge not available for DOM test: {error}")
        source = inspect.getsource(_scrape_reference_browser_locked)
        cls.script = re.search(
            r'evaluate_after_navigation\(browser_page, r"""(.*?)"""', source, re.S
        ).group(1)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def snapshot(self, html):
        page = self.browser.new_page()
        try:
            page.route("**/*", lambda route: route.abort())
            page.set_content(html)
            return page.evaluate(self.script)
        finally:
            page.close()

    def test_widgetless_gallery_reads_rail_without_recommendations(self):
        result = self.snapshot('''
            <h1 style="position:absolute;left:550px;top:300px">Current Product</h1>
            <img style="position:absolute;left:100px;top:310px;width:400px;height:500px"
                 src="https://ir.ozone.ru/s3/multimedia-a/wc1000/main.jpg">
            <img style="position:absolute;left:25px;top:310px;width:60px;height:80px"
                 src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
                 data-src="https://ir.ozone.ru/s3/multimedia-a/wc100/main.jpg">
            <img style="position:absolute;left:25px;top:410px;width:60px;height:80px"
                 src="https://ir.ozone.ru/s3/multimedia-a/wc100/second.jpg">
            <img style="position:absolute;left:1000px;top:310px;width:100px;height:100px"
                 src="https://ir.ozone.ru/s3/multimedia-a/wc100/recommendation.jpg">
        ''')
        self.assertEqual(result["title"], "Current Product")
        self.assertEqual([url.rsplit("/", 1)[-1] for url in result["images"]],
                         ["main.jpg", "second.jpg"])

    def test_no_heading_does_not_match_every_alt(self):
        result = self.snapshot('''<img alt="Other product"
            src="https://ir.ozone.ru/s3/multimedia-a/wc100/other.jpg">''')
        self.assertEqual(result["images"], [])

    def test_structured_image_objects_are_read(self):
        result = self.snapshot('''<script type="application/ld+json">
            {"@type":"Product","name":"Current Product",
             "image":[{"url":"https://ir.ozone.ru/s3/multimedia-a/wc100/main.jpg"}]}
            </script>''')
        self.assertEqual(len(result["images"]), 1)


if __name__ == "__main__":
    unittest.main()
