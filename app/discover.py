"""按数据源配置翻页，发现数据集详情页 URL。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


@dataclass
class DiscoveryResult:
    source: str
    pages: list[object] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def discovered(self) -> int:
        return len(self.entries)


def _page_html(page: object) -> str:
    if isinstance(page, str):
        return page
    html = getattr(page, "html", None)
    if html is None:
        raise TypeError("fetch 返回值必须包含 html 属性或为字符串")
    return html


def _with_query(url: str, param: str, value: int) -> str:
    parts = urlsplit(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param]
    pairs.append((param, str(value)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), ""))


def _start_url(base_url: str, start_path: str, pagination: dict) -> str:
    url = urljoin(base_url.rstrip("/") + "/", start_path.lstrip("/"))
    if pagination.get("type", "none") == "query":
        param = str(pagination.get("param", "page"))
        start = int(pagination.get("start", 1))
        return _with_query(url, param, start)
    return url


def _item_id(url: str, pattern: str | None) -> str:
    if pattern:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else url


def discover_source(
    fetch: Callable[[str, str, str], object],
    source: str,
    config: dict,
) -> DiscoveryResult:
    """翻页并返回详情页集合。URL 去重，页数受 max_pages 硬约束。"""
    pagination = config.get("pagination") or {}
    ptype = str(pagination.get("type", "none")).lower()
    max_pages = max(1, min(50, int(pagination.get("max_pages", 1))))
    start = int(pagination.get("start", 1))
    step = max(1, int(pagination.get("step", 1)))
    param = str(pagination.get("param", "page"))
    list_cfg = config.get("list") or {}
    item_selector = list_cfg.get("item_selector") or "a"
    link_selector = list_cfg.get("link_selector")
    id_pattern = list_cfg.get("item_id_regex")
    next_selector = pagination.get("next_selector") or list_cfg.get("next_selector")
    base_url = str(config.get("base_url", ""))
    start_path = str(config.get("start_path", "/"))

    current = _start_url(base_url, start_path, pagination)
    current_page = start
    visited_pages: set[str] = set()
    seen_details: set[str] = set()
    result = DiscoveryResult(source=source)

    while current and len(result.pages) < max_pages:
        if current in visited_pages:
            break
        visited_pages.add(current)
        raw = fetch(current, source, "list")
        html = _page_html(raw)
        result.pages.append(raw)
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(item_selector)
        found_this_page = 0
        for item in items:
            link = item.select_one(link_selector) if link_selector else item
            if link is None:
                continue
            href = link.get("href") or (item.get("href") if item.name == "a" else None)
            if not href:
                continue
            detail_url = urljoin(current, href).split("#", 1)[0]
            if not detail_url or detail_url in seen_details:
                continue
            seen_details.add(detail_url)
            result.entries.append(
                {
                    "url": detail_url,
                    "item_id": _item_id(detail_url, id_pattern),
                    "source": source,
                }
            )
            found_this_page += 1

        if not found_this_page:
            break
        if ptype == "query":
            current_page += step
            current = _with_query(current, param, current_page)
        elif ptype == "next_link" and next_selector:
            node = soup.select_one(next_selector)
            href = node.get("href") if node is not None else None
            current = urljoin(current, href).split("#", 1)[0] if href else ""
        else:
            break

    return result


def source_summary(result: DiscoveryResult, elapsed_ms: int) -> dict:
    """供 crawl_run 落库的摘要。"""
    return {
        "source": result.source,
        "pages": result.page_count,
        "discovered": result.discovered,
        "elapsed_ms": elapsed_ms,
        "urls": [entry["url"] for entry in result.entries],
    }
