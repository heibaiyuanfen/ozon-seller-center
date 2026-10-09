"""Single collection per product batch across prefetch and worker processes."""
import hashlib
import os
import time
from pathlib import Path
from core import atomic_write_json, load_json


def collect_shared_source(root, key, collect, cancel_check=lambda: None, log=lambda _text: None):
    directory = Path(root) / hashlib.sha256(key.encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / "source.json"
    with (directory / "source.lock").open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        waiting = False
        while True:
            cancel_check()
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
                if not waiting:
                    log("等待同商品的共享采集和原图下载；本任务不会重复启动采集")
                    waiting = True
                time.sleep(0.2)
        try:
            state = load_json(cache, {})
            images = state.get("source_images") if isinstance(state, dict) else None
            if images and state.get("reference") and all(Path(path).is_file() for path in images):
                log("已复用同商品共享原图和商品信息，跳过页面采集及图片下载")
                return state
            cancel_check()
            state = collect()
            if not state.get("source_images") or not all(Path(path).is_file() for path in state["source_images"]):
                raise RuntimeError("共享采集没有生成完整的商品原图")
            atomic_write_json(cache, state)
            return state
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
