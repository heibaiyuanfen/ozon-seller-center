"""Offline browser and image-download checks for a clean installation."""
import io
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
from browser_runtime import chromium_launch_candidates


def check_browser_images():
    payload = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(payload, format="JPEG")
    picture = payload.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            is_image = self.path == "/product.jpg"
            body = picture if is_image else b'<html><title>collector-test</title><img src="/product.jpg"></html>'
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg" if is_image else "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with tempfile.TemporaryDirectory(prefix="rfbs-browser-check-") as folder:
            with sync_playwright() as playwright:
                # Use the same first candidate as production, without falling
                # back to a developer machine's installed browser.
                context = playwright.chromium.launch_persistent_context(
                    str(Path(folder) / "profile"), headless=True,
                    **chromium_launch_candidates()[0],
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/", timeout=15000)
                    page.wait_for_function("document.images[0].naturalWidth === 600", timeout=10000)
                    url = page.locator("img").evaluate("img => img.currentSrc")
                    response = context.request.get(url, timeout=10000)
                    if not response.ok:
                        raise RuntimeError("Browser image download failed")
                    target = Path(folder) / "download.jpg"
                    target.write_bytes(response.body())
                    with Image.open(target) as image:
                        image.load()
                        if image.size != (600, 800):
                            raise RuntimeError("Downloaded image decoding failed")
                finally:
                    context.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
