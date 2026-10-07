from __future__ import annotations

import os
import traceback
import tkinter as tk
from pathlib import Path

from core import atomic_write_json, load_json


def run_parallel_job(request_path: str, result_path: str) -> int:
    """Run one complete listing in an isolated Tk/Python process."""
    os.environ["OZON_PARALLEL_WORKER"] = "1"
    from app import AutoJobCancelled, RfbsListingApp

    payload = load_json(request_path, {})
    job = payload.get("job") if isinstance(payload, dict) else None
    if not isinstance(job, dict) or not job.get("id"):
        atomic_write_json(result_path, {"ok": False, "error": "并行任务文件无效"})
        return 2

    root = tk.Tk()
    root.withdraw()
    app = RfbsListingApp(root)
    app._refresh_job_tree = lambda: None
    app._save_workspace_quietly = lambda: None
    app.auto_jobs = {str(job["id"]): job}
    app.auto_job_order = [str(job["id"])]
    app.active_job_id = str(job["id"])
    def save_progress():
        atomic_write_json(result_path, {"ok": None, "job": job})
    cancel_path = Path(str(payload.get("cancel_path") or request_path + ".cancel.json"))
    original_cancel_check = app._check_auto_job_cancelled
    def check_cancel(current_job):
        if cancel_path.is_file():
            current_job["cancel_requested"] = True
            current_job["status"] = "stopping"
        original_cancel_check(current_job)
    def ui_call(callback, timeout=30):
        result = callback()
        root.update()
        return result
    app._save_auto_jobs = save_progress
    app._ui_call = ui_call
    app._check_auto_job_cancelled = check_cancel
    try:
        app._run_auto_job(job)
        job.update({
            "status": "completed", "stage": 7, "error": "",
            "message": "上架、RFBS 库存和 Excel 台账已完成",
        })
        atomic_write_json(result_path, {"ok": True, "job": job})
        return 0
    except AutoJobCancelled as error:
        job.update({
            "status": "cancelled", "cancel_requested": False, "error": "",
            "message": str(error) or "任务已按要求停止",
        })
        atomic_write_json(result_path, {"ok": True, "job": job})
        return 0
    except Exception as error:
        job["status"] = "failed"
        job["failed_stage"] = min(7, int(job.get("stage") or 0) + 1)
        job["error"] = str(error) or type(error).__name__
        job["message"] = "点击“修复选中任务”修改后继续"
        atomic_write_json(result_path, {
            "ok": False, "job": job, "error": job["error"],
            "traceback": traceback.format_exc(),
        })
        return 1
    finally:
        app.prefetch_executor.shutdown(wait=False, cancel_futures=True)
        root.destroy()
