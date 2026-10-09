"""Validate the packaged CA bundle before any HTTPS clients are initialized."""
import os
import ssl
import sys
from pathlib import Path

import certifi


def _valid_ca_path(path):
    if not path:
        return False
    target = Path(path)
    try:
        if target.is_dir():
            ssl.create_default_context(capath=str(target))
        elif target.is_file():
            ssl.create_default_context(cafile=str(target))
        else:
            return False
    except (OSError, ssl.SSLError):
        return False
    return True


def configure_tls_ca_bundle():
    candidates = [certifi.where()]
    if getattr(sys, "_MEIPASS", None):
        candidates.append(str(Path(sys._MEIPASS) / "certifi" / "cacert.pem"))
    bundle = next((str(Path(path).resolve()) for path in candidates if _valid_ca_path(path)), None)
    if bundle is None:
        raise RuntimeError("HTTPS CA 证书文件缺失或损坏，请使用完整发布目录启动程序")
    for key in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        if not _valid_ca_path(os.environ.get(key)):
            os.environ[key] = bundle
    return bundle
