from __future__ import annotations

import os
import sys
from pathlib import Path
from contextlib import contextmanager
import time


@contextmanager
def browser_profile_lock(profile: Path):
    """Serialize browser access across threads and listing subprocesses."""
    profile.mkdir(parents=True, exist_ok=True)
    with (profile / "collector.lock").open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                time.sleep(0.2)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def evaluate_after_navigation(page, script, deadline):
    """Keep the same verification window when navigation interrupts DOM reads."""
    while True:
        try:
            return page.evaluate(script)
        except Exception as error:
            if not any(marker in str(error).lower() for marker in (
                "execution context was destroyed", "cannot find context with specified id",
            )) or page.is_closed() or time.monotonic() >= deadline:
                raise
            page.wait_for_timeout(500)


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
    """Prefer the shipped runtime; allow installed browsers as fallbacks."""
    candidates = []
    bundled = bundled_edge_executable()
    if bundled is not None:
        candidates.append({"executable_path": str(bundled)})
    candidates.extend([{"channel": "chrome"}, {"channel": "msedge"}])
    return candidates


def bundled_edge_executable() -> Path | None:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    executable = root / "edge-runtime" / "msedge.exe"
    return executable if executable.is_file() else None
