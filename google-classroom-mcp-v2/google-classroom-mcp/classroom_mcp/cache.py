"""Cache simples em disco (JSON) com validade, para nao baixar tudo de novo."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Optional

from .config import CACHE_DIR, CACHE_TTL_SECONDS
from .models import FileItem


def _path(key: str):
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return CACHE_DIR / (h + ".json")


def get_items(key: str) -> Optional[list[FileItem]]:
    p = _path(key)
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) > CACHE_TTL_SECONDS:
            return None
        return [FileItem.from_json(d) for d in data["items"]]
    except Exception:
        return None


def put_items(key: str, items: list[FileItem]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _path(key).write_text(
            json.dumps({"ts": time.time(), "items": [i.to_json() for i in items]}),
            encoding="utf-8",
        )
    except Exception:
        pass  # cache e opcional


def clear() -> int:
    n = 0
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink(missing_ok=True)
            n += 1
    return n
