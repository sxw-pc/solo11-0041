"""抓取留档的稳定标识：由 source、kind、item_id 派生。

详情页的 item_id 取最终 URL 路径末段；列表页没有 item_id 时回退到 URL 指纹，
以保证同一 run 内同一逻辑页面的多次留档落在同一个文件与同一条审计记录上。
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit


def item_id_from_url(url: str) -> str:
    """返回 URL 路径末段；路径为空时返回空串。"""
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def derive_item_id(url: str, kind: str = "detail") -> str:
    if kind != "list":
        segment = item_id_from_url(url)
        if segment:
            return segment
    return "url-" + hashlib.sha1(url.encode("utf-8")).hexdigest()


def stable_archive_id(source: str, kind: str, item_id: str) -> str:
    payload = f"{source}|{kind}|{item_id}"
    return "arc_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]
