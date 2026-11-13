"""详情页元数据抽取：结构选择器优先，缺失时使用受控启发式回退。"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

FIELD_ALIASES = {
    "title": ["dataset title", "title"],
    "category": ["category", "topic", "类别", "主题"],
    "publisher": ["publisher", "published by", "agency", "发布方", "发布机构"],
    "temporal": ["time coverage", "time period", "temporal coverage", "time span", "period", "时间范围"],
    "geography": ["geography", "geographic scope", "spatial coverage", "geographic coverage", "地理范围"],
    "license": ["license", "licence", "usage license", "许可"],
    "update_frequency": ["update cadence", "update frequency", "refresh cadence", "frequency", "更新频率"],
}

FORMAT_EXTENSIONS = {"csv": "CSV", "json": "JSON", "jsonl": "JSON", "xml": "XML", "geojson": "GEOJSON",
                     "xlsx": "XLSX", "xls": "XLSX", "zip": "ZIP", "pdf": "PDF", "parquet": "PARQUET", "html": "HTML"}
SIZE_PATTERN = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:bytes?|kb|kib|mb|mib|gb|gib|tb)\b", re.IGNORECASE)


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _key(value: str) -> str:
    return re.sub(r"[\s:._-]+", " ", value.strip().lower()).strip()


def _node_location(node: Tag) -> str:
    segments: list[str] = []
    current: Tag | None = node
    while current is not None and current.name not in {"html", "body", "[document]"} and len(segments) < 4:
        part = current.name or ""
        if current.get("id"):
            segments.append(f"{part}#{current['id']}")
            break
        classes = [str(c) for c in (current.get("class") or []) if c]
        if classes:
            part += "." + classes[0]
        parent = current.parent if isinstance(current.parent, Tag) else None
        if parent is not None:
            same = [child for child in parent.find_all(current.name, recursive=False)]
            if len(same) > 1:
                part += f":nth-of-type({same.index(current) + 1})"
        segments.append(part)
        current = parent
    return " > ".join(reversed(segments)) or (node.name or "node")


def _trace(node: Tag | None, selector: str, fallback: bool = False) -> dict:
    text = _clean(node.get_text(" ", strip=True)) if node is not None else ""
    return {
        "selector": selector,
        "fallback": fallback,
        "location": _node_location(node) if node is not None else "",
        "snippet": text[:160],
    }


def _spec_parts(spec: object) -> tuple[str | None, str | None]:
    if isinstance(spec, str):
        return spec, None
    if isinstance(spec, dict):
        return spec.get("selector"), spec.get("attr")
    return None, None


def _select(soup: BeautifulSoup, spec: object) -> tuple[str | None, str | None, Tag | None]:
    selector, attr = _spec_parts(spec)
    if not selector:
        return selector, attr, None
    node = soup.select_one(selector)
    if node is None:
        return selector, attr, None
    value = node.get(attr) if attr else _clean(node.get_text(" ", strip=True))
    if not _clean(value):
        return selector, attr, None
    return selector, attr, node


def _key_value_pairs(soup: BeautifulSoup) -> dict[str, tuple[str, Tag]]:
    pairs: dict[str, tuple[str, Tag]] = {}
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is None:
            dd = dt.find_next("dd")
        if isinstance(dd, Tag):
            label = _clean(dt.get_text(" ", strip=True)).rstrip(":")
            if label:
                pairs.setdefault(_key(label), (label, dd))
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = _clean(cells[0].get_text(" ", strip=True)).rstrip(":")
        if label and cells[1].find(["h1", "h2", "h3"]):
            continue
        if label:
            pairs.setdefault(_key(label), (label, cells[1]))
    return pairs


def _find_kv(pairs: dict[str, tuple[str, Tag]], field: str) -> tuple[str, Tag] | None:
    for alias in FIELD_ALIASES.get(field, []):
        normalized = _key(alias)
        if normalized in pairs:
            return pairs[normalized]
    for alias in FIELD_ALIASES.get(field, []):
        normalized = _key(alias)
        for key, item in pairs.items():
            if normalized and normalized in key:
                return item
    return None


def _field_rows(soup: BeautifulSoup, config: dict) -> tuple[list[dict], dict]:
    spec = (config.get("detail") or {}).get("field_table") or {}
    selector = spec.get("selector") or "table tbody tr"
    rows = soup.select(selector)
    result: list[dict] = []
    for index, row in enumerate(rows):
        if spec.get("name_selector"):
            name_node = row.select_one(spec["name_selector"])
            type_node = row.select_one(spec.get("type_selector", "")) if spec.get("type_selector") else None
            desc_node = row.select_one(spec.get("description_selector", "")) if spec.get("description_selector") else None
        else:
            cells = row.find_all(["td", "th"], recursive=False)
            name_node, type_node, desc_node = (cells + [None, None, None])[:3]
        name = _clean(name_node.get_text(" ", strip=True)) if name_node else ""
        if not name or name.lower() in {"field", "column", "name"}:
            continue
        result.append(
            {
                "name": name,
                "type": _clean(type_node.get_text(" ", strip=True)) if type_node else "",
                "description": _clean(desc_node.get_text(" ", strip=True)) if desc_node else "",
                "source_selector": selector,
                "location": f"{selector} row {index + 1}",
            }
        )
    if result:
        return result, {"selector": selector, "fallback": False, "count": len(result)}
    return _fallback_fields(soup)


def _fallback_fields(soup: BeautifulSoup) -> tuple[list[dict], dict]:
    for table in soup.find_all("table"):
        headers = [_key(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        name_i = next((i for i, h in enumerate(headers) if h in {"field", "column", "name"}), None)
        type_i = next((i for i, h in enumerate(headers) if "type" in h), None)
        if name_i is None or type_i is None or name_i == type_i:
            continue
        desc_i = next((i for i, h in enumerate(headers) if h in {"description", "notes", "comment"}), None)
        rows: list[dict] = []
        for index, row in enumerate(table.select("tbody tr")):
            cells = row.find_all(["td", "th"])
            if len(cells) <= max(name_i, type_i):
                continue
            name = _clean(cells[name_i].get_text(" ", strip=True))
            if not name:
                continue
            description = _clean(cells[desc_i].get_text(" ", strip=True)) if desc_i is not None and desc_i < len(cells) else ""
            rows.append(
                {
                    "name": name,
                    "type": _clean(cells[type_i].get_text(" ", strip=True)),
                    "description": description,
                    "source_selector": "heuristic:table[header]",
                    "location": f"table[header] row {index + 1}",
                }
            )
        if rows:
            return rows, {"selector": "heuristic:table[header]", "fallback": True, "count": len(rows)}
    return [], {"selector": None, "fallback": True, "count": 0}


def _extract_resources(soup: BeautifulSoup, config: dict, page_url: str) -> tuple[list[dict], dict]:
    spec = (config.get("detail") or {}).get("resources") or {}
    selector = spec.get("selector") or "a[href]"
    rows = soup.select(selector) if spec.get("selector") else []
    resources: list[dict] = []
    for index, row in enumerate(rows):
        link = row.select_one(spec.get("link_selector", "a")) if row.name != "a" else row
        href = link.get("href") if link is not None else None
        if not href:
            continue
        format_node = row.select_one(spec["format_selector"]) if spec.get("format_selector") else None
        size_node = row.select_one(spec["size_selector"]) if spec.get("size_selector") else None
        format_value = _clean(format_node.get_text()) if format_node else ""
        size_value = _clean(size_node.get_text()) if size_node else ""
        resources.append({"format": format_value, "size_raw": size_value, "url": urljoin(page_url, href),
                          "source_selector": selector, "location": f"{selector} row {index + 1}"})
    if resources:
        return resources, {"selector": selector, "fallback": False, "count": len(resources)}
    return _fallback_resources(soup, page_url)


def _fallback_resources(soup: BeautifulSoup, page_url: str) -> tuple[list[dict], dict]:
    resources: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href"))
        extension = Path(urlsplit(href).path).suffix.lower().lstrip(".")
        text = _clean(anchor.get_text(" ", strip=True))
        if extension not in FORMAT_EXTENSIONS and "download" not in text.lower():
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)
        context = _clean(anchor.parent.get_text(" ", strip=True)) if anchor.parent else text
        size_match = SIZE_PATTERN.search(context)
        if size_match:
            size_raw = _clean(size_match.group(0))
        else:
            guessed = re.search(r"\(([^)]*(?:size|unknown)[^)]*)\)", context, re.IGNORECASE)
            size_raw = _clean(guessed.group(1)) if guessed else ""
        resources.append({"format": FORMAT_EXTENSIONS.get(extension, ""), "size_raw": size_raw, "url": url,
                          "source_selector": "heuristic:a[href$=resource]", "location": _node_location(anchor)})
    return resources, {"selector": "heuristic:a[href$=resource]", "fallback": True, "count": len(resources)}


def extract_detail(html: str, page_url: str, source: str, config: dict) -> dict:
    """抽取单条详情记录，并返回字段级/列表级溯源信息。"""
    soup = BeautifulSoup(html, "html.parser")
    detail_cfg = config.get("detail") or {}
    values: dict[str, str | None] = {}
    traces: dict[str, dict] = {}

    for field, spec in (detail_cfg.get("selectors") or {}).items():
        selector, _attr, node = _select(soup, spec)
        if node is not None:
            values[field] = _clean(node.get_text(" ", strip=True))
            traces[field] = _trace(node, selector or "")
        else:
            values[field] = None

    if not values.get("title"):
        node = soup.find("h1")
        if node:
            values["title"] = _clean(node.get_text(" ", strip=True))
            traces["title"] = _trace(node, "heuristic:h1", True)

    pairs = _key_value_pairs(soup)
    for field in ("category", "publisher", "temporal", "geography", "license", "update_frequency"):
        if values.get(field):
            continue
        match = _find_kv(pairs, field)
        if match:
            label, node = match
            values[field] = _clean(node.get_text(" ", strip=True))
            traces[field] = _trace(node, f"heuristic:key-value[{label}]", True)

    if not values.get("publisher"):
        node = soup.select_one(".publisher, .agency, [rel='author']")
        if node:
            values["publisher"] = _clean(node.get_text(" ", strip=True))
            traces["publisher"] = _trace(node, "heuristic:[class*=publisher]", True)
    if not values.get("category"):
        node = soup.select_one(".breadcrumb a:last-of-type, nav a:last-of-type")
        if node:
            values["category"] = _clean(node.get_text(" ", strip=True))
            traces["category"] = _trace(node, "heuristic:breadcrumb", True)

    fields, field_list_trace = _field_rows(soup, config)
    resources, resource_list_trace = _extract_resources(soup, config, page_url)
    fallback_used = any(t.get("fallback") for t in traces.values()) or bool(
        field_list_trace.get("fallback") or resource_list_trace.get("fallback")
    )
    extraction = {
        "fields": traces,
        "lists": {"fields": field_list_trace, "resources": resource_list_trace},
        "fallback_used": fallback_used,
        "page_title": _clean(soup.title.get_text(" ", strip=True)) if soup.title else "",
    }
    return {
        "source": source,
        "source_url": page_url,
        "title": values.get("title"),
        "category": values.get("category"),
        "publisher": values.get("publisher"),
        "temporal": values.get("temporal"),
        "geography": values.get("geography"),
        "license": values.get("license"),
        "update_frequency": values.get("update_frequency"),
        "fields": fields,
        "resources": resources,
        "extraction": extraction,
    }
