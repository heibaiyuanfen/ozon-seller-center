import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from amazon_source import (
    AmazonProduct, download_amazon_images, normalize_amazon_source, parse_amazon_html,
)
from services import ImageGenerationService


class AmazonSourceTests(unittest.TestCase):
    def test_normalizes_asin_and_international_product_link(self):
        asin, url = normalize_amazon_source("B0ABC12345")
        self.assertEqual(asin, "B0ABC12345")
        self.assertEqual(url, "https://www.amazon.com/dp/B0ABC12345")
        asin, url = normalize_amazon_source(
            "https://www.amazon.de/gp/product/B0ABC12345/ref=something"
        )
        self.assertEqual(asin, "B0ABC12345")
        self.assertEqual(url, "https://www.amazon.de/dp/B0ABC12345")

    def test_parses_ordered_high_resolution_gallery(self):
        page = r'''
        <script type="application/ld+json">
        {"@type":"Product","name":"Test product","image":["https://m.media-amazon.com/images/I/first.jpg"]}
        </script>
        <script>
        var colorImages = {"initial":[
          {"hiRes":"https://m.media-amazon.com/images/I/second._AC_SL1500_.jpg"},
          {"large":"https://m.media-amazon.com/images/I/third.jpg"}
        ]};
        </script>
        '''
        product = parse_amazon_html(
            "B0ABC12345", "https://www.amazon.com/dp/B0ABC12345", page,
        )
        self.assertEqual(product.title, "Test product")
        self.assertEqual(product.images, [
            "https://m.media-amazon.com/images/I/first.jpg",
            "https://m.media-amazon.com/images/I/second.jpg",
            "https://m.media-amazon.com/images/I/third.jpg",
        ])

    def test_download_uses_separate_asin_batch_and_numbered_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            def fake_download(_self, _urls, output_dir, limit=15, log_func=None):
                directory = Path(output_dir)
                directory.mkdir(parents=True, exist_ok=True)
                paths = []
                for name in ("download-a.jpg", "download-b.jpg"):
                    path = directory / name
                    path.write_bytes(b"image")
                    paths.append(str(path))
                return paths

            product = AmazonProduct(
                "B0ABC12345", "https://www.amazon.com/dp/B0ABC12345", "Test",
                ["https://m.media-amazon.com/a.jpg"],
            )
            with patch("amazon_source.ImageDownloadService.download", fake_download):
                paths, directory = download_amazon_images(product, temp_dir)
            self.assertEqual(directory.parent.name, "B0ABC12345")
            self.assertEqual([Path(path).name for path in paths], ["01.jpg", "02.jpg"])
            self.assertTrue(all(Path(path).parent == directory for path in paths))


class _ImageResponse:
    status_code = 200
    text = ""

    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"b64_json": base64.b64encode(self._content).decode()}]}


class _ImageSession:
    def __init__(self, content):
        self.content = content

    def post(self, *args, **kwargs):
        return _ImageResponse(self.content)


class AmazonGeneratedMainTests(unittest.TestCase):
    def test_generated_amazon_main_is_exact_three_by_four(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.jpg"
            Image.new("RGB", (900, 900), "white").save(reference)
            buffer = io.BytesIO()
            Image.new("RGB", (1792, 2400), "blue").save(buffer, "PNG")
            result = ImageGenerationService(
                "https://example.com/v1/images/edits", "key", "model",
                session=_ImageSession(buffer.getvalue()),
            ).generate([str(reference)], "prompt", 1, str(root / "out"), exact_3_4=True)
            with Image.open(result[0]) as image:
                self.assertEqual(image.width * 4, image.height * 3)


if __name__ == "__main__":
    unittest.main()
