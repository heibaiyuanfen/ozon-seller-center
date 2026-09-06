"""Independent local storage for Mercado Libre drafts and planning artifacts."""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

from PIL import Image

from .models import ListingPlan, Picture, ProductDraft, utc_now


SCHEMA_VERSION = 1
MAX_IMAGE_BYTES = 10 * 1024 * 1024
SETTINGS_FIELDS = frozenset({"app_id", "redirect_uri", "account_label"})
_DRAFT_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_IMAGE_NUMBER = re.compile(r"([0-9]+)_", re.ASCII)


class MercadoLibreStore:
    """All persistent state lives below the caller's Mercado Libre data root.

    Access tokens and client secrets deliberately have no storage interface.
    Broken files are retained for recovery and reported through ``warnings``.
    """

    def __init__(self, root: Path):
        requested = Path(root).absolute()
        if self._is_link(requested):
            raise ValueError("美客多数据目录不能是符号链接或目录联接")
        self.root = requested.resolve()
        self.warnings: list[str] = []

    @staticmethod
    def _is_link(path: Path) -> bool:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        # Windows directory junctions are reparse points, not ordinary symlinks.
        return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)

    @staticmethod
    def _validate_id(draft_id: str) -> str:
        if not isinstance(draft_id, str) or not _DRAFT_ID.fullmatch(draft_id):
            raise ValueError("美客多草稿 ID 必须为 32 位小写十六进制字符")
        return draft_id

    def _check_path(self, path: Path) -> Path:
        try:
            relative = path.relative_to(self.root)
            path.resolve().relative_to(self.root)
        except ValueError as exc:
            raise ValueError("美客多数据路径超出独立目录") from exc
        current = self.root
        for part in (None, *relative.parts):
            if part is not None:
                current /= part
            if self._is_link(current):
                raise ValueError("美客多数据路径不能包含符号链接或目录联接")
        return path

    def _product_path(self, draft_id: str) -> Path:
        return self._check_path(self.root / "products" / self._validate_id(draft_id))

    def _mkdir(self, path: Path) -> None:
        self._check_path(path)
        path.mkdir(parents=True, exist_ok=True)
        self._check_path(path)

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _read_json(self, path: Path) -> dict:
        self._check_path(path)
        with path.open("r", encoding="utf-8-sig") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("JSON 顶层必须是对象")
        if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
            raise ValueError("不支持的美客多本地数据版本")
        return data

    def _atomic_json(self, path: Path, data: dict, before_replace: Callable | None = None) -> Path:
        self._check_path(path)
        text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        self._mkdir(path.parent)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix=".tmp-", suffix=".json", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            self._check_path(path)
            if before_replace is not None:
                before_replace()
            os.replace(temporary, path)
            return path
        finally:
            if temporary is not None and temporary.exists():
                self._check_path(temporary)
                temporary.unlink()

    def _read_draft(self, path: Path, draft_id: str) -> ProductDraft:
        result = ProductDraft.from_dict(self._read_json(path))
        if result.id != draft_id:
            raise ValueError("草稿内容 ID 与产品目录不一致")
        self._validate_id(result.id)
        return result

    def save_draft(self, draft: ProductDraft) -> Path:
        # Validate nested types and versions before creating any files.
        payload = draft.to_dict()
        validated = ProductDraft.from_dict(payload)
        if type(validated.schema_version) is not int or validated.schema_version != SCHEMA_VERSION:
            raise ValueError("不支持的美客多本地数据版本")
        path = self._product_path(validated.id) / "draft.json"

        def preserve_unreadable() -> None:
            self._check_path(path)
            if path.exists():
                try:
                    self._read_draft(path, validated.id)
                except (OSError, ValueError, TypeError) as exc:
                    self._warn(f"草稿 {validated.id} 无法读取，已保留原文件。")
                    raise ValueError("已有草稿无法读取，请恢复原文件或另存为新草稿") from exc

        preserve_unreadable()
        payload["updated_at"] = utc_now()
        result = self._atomic_json(path, payload, before_replace=preserve_unreadable)
        draft.updated_at = payload["updated_at"]
        return result

    def load_draft(self, draft_id: str) -> ProductDraft:
        path = self._product_path(draft_id) / "draft.json"
        try:
            return self._read_draft(path, draft_id)
        except (OSError, ValueError, TypeError):
            self._warn(f"草稿 {draft_id} 无法读取，已保留原文件。")
            raise

    def list_drafts(self) -> list[ProductDraft]:
        drafts = []
        try:
            products = self._check_path(self.root / "products")
            if not products.exists():
                return []
            entries = sorted(products.iterdir())
        except (OSError, ValueError):
            self._warn("美客多产品目录无法读取，请检查目录权限。")
            return []
        for directory in entries:
            if not _DRAFT_ID.fullmatch(directory.name):
                continue
            try:
                self._check_path(directory)
                if directory.is_dir() and (directory / "draft.json").exists():
                    drafts.append(self.load_draft(directory.name))
            except (OSError, ValueError, TypeError):
                self._warn(f"草稿 {directory.name} 无法读取，已保留原文件。")
        return sorted(drafts, key=lambda item: (item.updated_at, item.id), reverse=True)

    def save_plan(self, plan: ListingPlan) -> Path:
        folder = self._product_path(plan.draft_id) / "plans"
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex + ".json"
        payload = {"schema_version": SCHEMA_VERSION, "plan": plan.to_dict()}
        return self._atomic_json(folder / name, payload)

    @staticmethod
    def _validated_image(path: Path) -> tuple[bytes, str]:
        if not path.is_file():
            raise ValueError(f"图片不是普通文件：{path.name}")
        with path.open("rb") as stream:
            content = stream.read(MAX_IMAGE_BYTES + 1)
        if not content or len(content) > MAX_IMAGE_BYTES:
            raise ValueError(f"图片必须非空且不超过 10 MB：{path.name}")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as picture:
                    image_format = picture.format
                    if image_format not in {"JPEG", "PNG"}:
                        raise ValueError("仅支持 JPEG 或 PNG 图片")
                    picture.verify()
                with Image.open(io.BytesIO(content)) as picture:
                    picture.load()
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError(f"无效的 JPEG/PNG 图片：{path.name}") from exc
        return content, ".jpg" if image_format == "JPEG" else ".png"

    def import_images(self, draft_id: str, paths: Iterable[Path | str]) -> list[Picture]:
        folder = self._product_path(draft_id) / "images"
        # Read and validate the whole batch before writing the first copy.
        images = [self._validated_image(Path(path)) for path in paths]
        if not images:
            return []
        self._mkdir(folder)
        existing = [
            int(match.group(1))
            for path in folder.iterdir()
            if (match := _IMAGE_NUMBER.match(path.name))
        ]
        number = max(existing, default=0) + 1
        results: list[Picture] = []
        written: list[Path] = []
        try:
            for content, extension in images:
                while True:
                    destination = self._check_path(folder / f"{number:02d}_{uuid4().hex[:8]}{extension}")
                    try:
                        stream = destination.open("xb")
                    except FileExistsError:
                        continue
                    break
                written.append(destination)
                with stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                results.append(Picture(local_path=str(destination.resolve())))
                number += 1
        except Exception:
            for path in written:
                self._check_path(path)
                path.unlink(missing_ok=True)
            raise
        return results

    @staticmethod
    def _validate_settings(settings: dict) -> dict:
        if not isinstance(settings, dict) or set(settings) - SETTINGS_FIELDS:
            raise ValueError("美客多本地设置仅支持 app_id、redirect_uri 和 account_label；不保存密钥或令牌")
        if any(not isinstance(value, str) for value in settings.values()):
            raise ValueError("美客多本地设置值必须是文本")
        return dict(settings)

    def load_settings(self) -> dict:
        path = self._check_path(self.root / "settings.json")
        if not path.exists():
            return {}
        try:
            payload = self._read_json(path)
            if set(payload) != {"schema_version", "settings"}:
                raise ValueError("未知的美客多设置字段")
            return self._validate_settings(payload["settings"])
        except (OSError, ValueError, TypeError):
            self._warn("美客多设置无法读取，已保留原文件；请检查设置文件。")
            return {}

    def save_settings(self, settings: dict) -> Path:
        allowed = self._validate_settings(settings)
        return self._atomic_json(
            self.root / "settings.json", {"schema_version": SCHEMA_VERSION, "settings": allowed},
        )
