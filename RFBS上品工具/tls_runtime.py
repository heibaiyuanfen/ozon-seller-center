"""Validate the packaged CA bundle before any HTTPS clients are initialized."""
import os
import ssl
import sys
import hashlib
import tempfile
from pathlib import Path

import certifi


def _short_path(path):
    if os.name != "nt":
        return str(path)
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return buffer.value if 0 < length < len(buffer) else str(path)


def _portable_bundle(path):
    """OpenSSL in frozen Windows builds can reject non-ASCII CA paths."""
    source = Path(path)
    if not source.is_file():
        return None
    short = _short_path(source.resolve())
    if _valid_ca_path(short):
        return short
    try:
        data = source.read_bytes()
        directory = Path(tempfile.gettempdir()) / "OzonRFBS-certificates"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (hashlib.sha256(data).hexdigest() + ".pem")
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        candidate = _short_path(target)
        return candidate if _valid_ca_path(candidate) else None
    except (OSError, ssl.SSLError, UnicodeError):
        return None


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
        bundle = next((value for path in candidates if (value := _portable_bundle(path))), None)
    if bundle is None:
        details = []
        for candidate in candidates:
            try:
                ssl.create_default_context(cafile=candidate)
            except Exception as error:
                details.append(f"{candidate}: {error}")
        raise RuntimeError("HTTPS CA 证书文件缺失或损坏，请使用完整发布目录启动程序；" + "; ".join(details))
    for key in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        if not _valid_ca_path(os.environ.get(key)):
            os.environ[key] = bundle
    return bundle
