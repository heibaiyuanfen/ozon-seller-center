from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from PIL import Image

from core import atomic_write_json, load_json


class CodexDesktopImages:
    desktop_handoff = True
    model = "codex-desktop"
    api_url = "codex-desktop://local-tasks"

    def __init__(self, task_root, metadata=None, log=None, cancel_check=lambda: None, timeout=1800):
        self.task_root = Path(task_root).resolve()
        self.metadata = metadata or {}
        self.log = log or (lambda message: None)
        self.cancel_check = cancel_check
        self.timeout = timeout

    def prepare(self, reference, prompt):
        reference = Path(reference)
        payload = {"prompt": prompt, "metadata": self.metadata,
                   "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest()}
        task_id = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        folder = self.task_root / task_id
        folder.mkdir(parents=True, exist_ok=True)
        copied = folder / ("reference" + reference.suffix.lower())
        if not copied.exists():
            shutil.copy2(reference, copied)
        request = folder / "request.json"
        if not request.exists():
            atomic_write_json(request, {"task_id": task_id, **payload,
                "reference_image": str(copied), "output_image": str(folder / "result.png"),
                "required_size": [900, 1200]})
            (folder / "Codex任务.txt").write_text(
                f"读取 {request} 中的需求和参考图。使用图片生成工具生成一张商品主图，"
                "保持商品主体准确，按 prompt 执行，输出尺寸 900x1200。"
                f"将最终图片保存为 {folder / 'result.png'}。完成保存后才写入 "
                f"{folder / 'result.json'}，内容为 "
                + json.dumps({"task_id": task_id, "status": "completed"}, ensure_ascii=False)
                + "。不要调用上架接口或修改其他任务。若失败，result.json 使用 status=failed 和 error 字段。",
                encoding="utf-8",
            )
        return folder, task_id

    def receive(self, folder, task_id, output_dir):
        result = load_json(folder / "result.json", {})
        if not isinstance(result, dict) or result.get("task_id") != task_id:
            return None
        if result.get("status") == "failed":
            raise RuntimeError("Codex 生图失败：" + str(result.get("error") or "未提供原因"))
        if result.get("status") != "completed":
            return None
        image_path = folder / "result.png"
        with Image.open(image_path) as image:
            image.load()
            if image.width * 4 != image.height * 3 or min(image.size) < 600:
                raise ValueError("Codex 回传图片必须为 3:4，短边至少 600 像素")
            destination = Path(output_dir)
            destination.mkdir(parents=True, exist_ok=True)
            output = destination / (task_id + ".png")
            image.convert("RGB").save(output, "PNG")
        return [str(output)]

    def generate(self, references, prompt, count, output_dir, exact_3_4=True):
        if len(references) != 1 or count != 1:
            raise ValueError("Codex 桌面模式每个任务使用一张主体参考图并生成一张主图")
        folder, task_id = self.prepare(references[0], prompt)
        self.log(f"等待 Codex 生图：{folder / 'Codex任务.txt'}；请在服务配置复制任务指令")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.cancel_check()
            result = self.receive(folder, task_id, output_dir)
            if result:
                self.log(f"Codex 图片已接收：{task_id}，继续水印和上传")
                return result
            time.sleep(1)
        raise TimeoutError("等待 Codex 回传超过 30 分钟；任务已保留，可返回图片后重试")
