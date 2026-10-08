import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser_runtime import bundled_chromium_executable, chromium_launch_candidates


class BrowserRuntimeTests(unittest.TestCase):
    def test_configured_playwright_browser_is_preferred(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            executable = (
                Path(temporary_directory)
                / "chromium-9999"
                / "chrome-win64"
                / "chrome.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch.dict(
                os.environ,
                {"PLAYWRIGHT_BROWSERS_PATH": temporary_directory, "LOCALAPPDATA": ""},
                clear=False,
            ):
                self.assertEqual(bundled_chromium_executable(), executable.resolve())
                candidates = chromium_launch_candidates()
            self.assertEqual(candidates[0], {"executable_path": str(executable.resolve())})
            self.assertEqual(candidates[-2:], [{"channel": "msedge"}, {"channel": "chrome"}])


if __name__ == "__main__":
    unittest.main()
