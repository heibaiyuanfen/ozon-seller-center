from __future__ import annotations

import os
import sys
from pathlib import Path


def bundled_chromium_executable() -> Path | None:
    """Return the Chromium executable shipped with the frozen application."""
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
        roots.append(Path(sys.executable).resolve().parent / "_internal")
    configured_root = str(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if configured_root and configured_root != "0":
        roots.append(Path(configured_root))
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        roots.append(Path(local_app_data) / "ms-playwright")

    relative_candidates = (
        Path("playwright-browsers"),
        Path("ms-playwright"),
        Path(),
    )
    executable_patterns = (
        "chromium-*/chrome-win64/chrome.exe",
        "chromium-*/chrome-win/chrome.exe",
    )
    seen: set[Path] = set()
    for root in roots:
        for relative in relative_candidates:
            search_root = (root / relative).resolve()
            if search_root in seen or not search_root.is_dir():
                continue
            seen.add(search_root)
            for pattern in executable_patterns:
                matches = sorted(search_root.glob(pattern), reverse=True)
                if matches:
                    return matches[0]
    return None


def chromium_launch_candidates() -> list[dict[str, str]]:
    """Prefer the packaged browser, retaining system browsers as fallbacks."""
    candidates: list[dict[str, str]] = []
    bundled = bundled_chromium_executable()
    if bundled is not None:
        candidates.append({"executable_path": str(bundled)})
    candidates.extend(({"channel": "msedge"}, {"channel": "chrome"}))
    return candidates
