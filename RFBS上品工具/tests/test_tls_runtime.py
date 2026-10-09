import os
import shutil
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import certifi
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tls_runtime import configure_tls_ca_bundle


class TlsRuntimeTests(unittest.TestCase):
    def test_invalid_old_directory_environment_uses_valid_ca(self):
        with patch.dict(os.environ, {"REQUESTS_CA_BUNDLE": "missing/old/cacert.pem", "CURL_CA_BUNDLE": "missing/cert.pem", "SSL_CERT_FILE": "missing/ssl.pem"}):
            bundle = configure_tls_ca_bundle()
            ssl.create_default_context(cafile=bundle)
            self.assertEqual(os.environ["REQUESTS_CA_BUNDLE"], bundle)
            self.assertEqual(os.environ["CURL_CA_BUNDLE"], bundle)
            self.assertEqual(os.environ["SSL_CERT_FILE"], bundle)

    def test_valid_user_supplied_ca_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            custom = str(Path(directory) / "custom.pem")
            shutil.copyfile(certifi.where(), custom)
            with patch.dict(os.environ, {"REQUESTS_CA_BUNDLE": custom}):
                configure_tls_ca_bundle()
                self.assertEqual(os.environ["REQUESTS_CA_BUNDLE"], custom)

    def test_missing_default_ca_uses_packaged_ca(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "certifi/cacert.pem"
            bundle.parent.mkdir()
            shutil.copyfile(certifi.where(), bundle)
            with patch("tls_runtime.certifi.where", return_value="missing.pem"), patch.object(sys, "_MEIPASS", directory, create=True), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(configure_tls_ca_bundle(), str(bundle.resolve()))
                self.assertEqual(os.environ["REQUESTS_CA_BUNDLE"], str(bundle.resolve()))

    def test_missing_all_bundles_fails_without_disabling_verification(self):
        with patch("tls_runtime.certifi.where", return_value="missing.pem"), patch.object(sys, "_MEIPASS", "missing-directory", create=True), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CA 证书文件缺失"):
                configure_tls_ca_bundle()
            self.assertNotIn("REQUESTS_CA_BUNDLE", os.environ)


if __name__ == "__main__":
    unittest.main()
