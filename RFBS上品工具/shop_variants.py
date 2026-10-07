"""Persistent identities and cover checks shared by listing worker processes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from PIL import Image, ImageOps


class DuplicateMainImageError(ValueError):
    pass


def shop_offer_id(base_offer_id: str, shop_id: str) -> str:
    """Keep a shop's offer stable when target order or batch identity changes."""
    base = str(base_offer_id).strip()
    shop = str(shop_id).strip()
    if not base or not shop:
        raise ValueError("生成店铺货号需要基础货号和店铺 ID")
    token = hashlib.sha256(json.dumps([base, shop], ensure_ascii=False).encode()).hexdigest()[:16].upper()
    return base[:83].rstrip("-") + "-" + token


def variant_directory(root: Path, group_id: str, shop_id: str) -> Path:
    # Hash the complete identities so Unicode and sanitized names cannot collide.
    group = hashlib.sha256(str(group_id).encode()).hexdigest()[:20]
    shop = hashlib.sha256(str(shop_id).encode()).hexdigest()[:20]
    return Path(root) / group / shop


def image_signature(path: str | Path) -> dict:
    path = Path(path)
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        sample = image.resize((32, 32), Image.Resampling.LANCZOS)
        gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = gray.tobytes()
        bits = sum(
            (pixels[row * 9 + col] > pixels[row * 9 + col + 1]) << (row * 8 + col)
            for row in range(8) for col in range(8)
        )
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "dhash": f"{bits:016x}", "sample": sample.tobytes().hex(),
        }


def similar_main_images(first: dict, second: dict) -> bool:
    if first["sha256"] == second["sha256"]:
        return True
    distance = (int(first["dhash"], 16) ^ int(second["dhash"], 16)).bit_count()
    if distance > 4:
        return False
    a, b = bytes.fromhex(first["sample"]), bytes.fromhex(second["sample"])
    # A color check prevents unrelated solid-background covers being rejected
    # merely because both have a low-information difference hash.
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) <= 8


class ShopVariantRegistry:
    """SQLite transactions make duplicate checks visible across subprocesses."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS main_images (
                group_id TEXT NOT NULL, shop_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, path TEXT NOT NULL,
                signature TEXT NOT NULL,
                PRIMARY KEY (group_id, shop_id)
            )""")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def cached_main(self, group_id: str, shop_id: str, fingerprint: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT path, signature FROM main_images WHERE group_id=? AND shop_id=? AND fingerprint=?",
                (group_id, shop_id, fingerprint),
            ).fetchone()
        if row:
            try:
                digest = hashlib.sha256(Path(row[0]).read_bytes()).hexdigest()
            except OSError:
                return None
            if digest == json.loads(row[1])["sha256"]:
                return row[0]
        return None

    def register_main(self, group_id: str, shop_id: str, fingerprint: str, path: str | Path):
        signature = image_signature(path)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT shop_id, signature FROM main_images WHERE group_id=? AND shop_id<>?",
                (group_id, shop_id),
            ).fetchall()
            if any(similar_main_images(signature, json.loads(other)) for _, other in rows):
                raise DuplicateMainImageError("该主图与本产品其他店铺主图相同或过于相似")
            connection.execute(
                "INSERT OR REPLACE INTO main_images VALUES (?, ?, ?, ?, ?)",
                (group_id, shop_id, fingerprint, str(Path(path).resolve()), json.dumps(signature)),
            )
