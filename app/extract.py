"""详情页元数据抽取：结构选择器优先，缺失时使用受控启发式回退。

字段取值不再“首命中即采用”：每个字段会沿配置 selector、``<h1>``、键值对、
发布方类名与面包屑等多条证据路径收集候选，记录在该字段 trace 的
``candidates`` 列表中；最终取值由 :func:`_arbitrate_candidates` 按
``detail.field_confidence`` 声明的路径权重与字段别名匹配度打分仲裁，
仲裁过程（得分、并列处理、落选原因）全部可解释、可留档。
"""

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

# 证据路径标识：selector 为配置驱动，其余均为受控启发式。
PATH_SELECTOR = "selector"
PATH_H1 = "heuristic:h1"
PATH_KEY_VALUE = "heuristic:key-value"
PATH_PUBLISHER = "heuristic:publisher-class"
PATH_BREADCRUMB = "heuristic:breadcrumb"
HEURISTIC_PATHS = {PATH_H1, PATH_KEY_VALUE, PATH_PUBLISHER, PATH_BREADCRUMB}

# 未配置 detail.field_confidence 时的默认路径顺序/权重。
DEFAULT_PATH_WEIGHTS = {
    PATH_SELECTOR: 100,
    PATH_H1: 70,
    PATH_KEY_VALUE: 60,
    PATH_PUBLISHER: 45,
    PATH_BREADCRUMB: 30,
}

# 别名匹配度加分（与路径权重相加，避免启发式仅靠路径顺序压过结构 selector）。
ALIAS_SCORE_EXACT = 20
ALIAS_SCORE_PARTIAL = 10
ALIAS_SCORE_NONE = 0

# 每个字段允许出现的候选路径（出现顺序即默认观察顺序，也是得分并列时的裁决顺序）。
FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "title": (PATH_SELECTOR, PATH_H1, PATH_KEY_VALUE),
    "category": (PATH_SELECTOR, PATH_KEY_VALUE, PATH_BREADCRUMB),
    "publisher": (PATH_SELECTOR, PATH_KEY_VALUE, PATH_PUBLISHER),
    "temporal": (PATH_SELECTOR, PATH_KEY_VALUE),
    "geography": (PATH_SELECTOR, PATH_KEY_VALUE),
    "license": (PATH_SELECTOR, PATH_KEY_VALUE),
    "update_frequency": (PATH_SELECTOR, PATH_KEY_VALUE),
}
ALL_FIELDS = tuple(FIELD_PATHS)


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


def _find_kv(pairs: dict[str, tuple[str, Tag]], field: str) -> tuple[str, Tag, str] | None:
    """返回 (原始标签, 值节点, 别名匹配级别 exact/partial)；无命中返回 None。"""
    for alias in FIELD_ALIASES.get(field, []):
        normalized = _key(alias)
        if normalized in pairs:
            label, node = pairs[normalized]
            return label, node, "exact"
    for alias in FIELD_ALIASES.get(field, []):
        normalized = _key(alias)
        for key, item in pairs.items():
            if normalized and normalized in key:
                return item[0], item[1], "partial"
    return None


def _path_weight(detail_cfg: dict, field: str, path: str) -> float:
    """读取 detail.field_confidence 声明的路径权重，缺失时回退默认顺序。"""
    confidence = detail_cfg.get("field_confidence") or {}
    field_block = confidence.get(field) if isinstance(confidence.get(field), dict) else None
    if field_block and path in field_block:
        return float(field_block[path])
    if path in confidence and not isinstance(confidence[path], dict):
        return float(confidence[path])
    return float(DEFAULT_PATH_WEIGHTS[path])


def _make_candidate(
    path: str,
    selector: str,
    node: Tag,
    match: str,
    order: int,
) -> dict:
    return {
        "value": _clean(node.get_text(" ", strip=True)),
        "path": path,
        "selector": selector,
        "match": match,
        "location": _node_location(node),
        "snippet": _clean(node.get_text(" ", strip=True))[:160],
        "order": order,
        "adopted": False,
        "rejected_reason": None,
    }


def _arbitrate_candidates(candidates: list[dict], detail_cfg: dict, field: str) -> dict | None:
    """按路径权重 + 别名匹配度打分仲裁；并列时取候选出现先后。

    落选候选写入 rejected_reason，全程为可解释的业务计算而非单条 if。
    """
    if not candidates:
        return None
    for candidate in candidates:
        alias_score = {
            "exact": ALIAS_SCORE_EXACT,
            "partial": ALIAS_SCORE_PARTIAL,
        }.get(candidate["match"], ALIAS_SCORE_NONE)
        weight = _path_weight(detail_cfg, field, candidate["path"])
        candidate["path_weight"] = weight
        candidate["alias_score"] = alias_score
        candidate["score"] = round(weight + alias_score, 6)
    top_score = max(item["score"] for item in candidates)
    tied = [item for item in candidates if item["score"] == top_score]
    winner = min(tied, key=lambda item: item["order"])
    winner["adopted"] = True
    for item in candidates:
        if item is winner:
            continue
        if item["score"] == winner["score"]:
            item["rejected_reason"] = (
                f"tie at score {item['score']} (path_weight={item['path_weight']}, "
                f"alias_score={item['alias_score']}); lost to earlier candidate "
                f"'{winner['path']}' by appearance order"
            )
        else:
            item["rejected_reason"] = (
                f"score {item['score']} (path_weight={item['path_weight']}, "
                f"alias_score={item['alias_score']}) below adopted score {winner['score']} "
                f"from '{winner['path']}'"
            )
    return winner


def _collect_field_candidates(
    soup: BeautifulSoup,
    field: str,
    detail_cfg: dict,
    pairs: dict[str, tuple[str, Tag]],
) -> tuple[list[dict], dict | None]:
    """沿该字段的全部证据路径收集非空、取值不同的候选。

    返回 (候选列表, 空启发式证据)。空启发式证据用于保留“回退路径被采用但
    页面未给出值”的溯源（例如空键值单元格），它不产生候选，只影响 fallback。
    """
    candidates: list[dict] = []
    blank_evidence: dict | None = None
    seen_values: set[str] = set()
    order = 0

    def add(path: str, selector: str, node: Tag | None, match: str) -> None:
        nonlocal order, blank_evidence
        if node is None:
            return
        value = _clean(node.get_text(" ", strip=True))
        if not value:
            # 仅启发式路径记录空证据；配置 selector 未命中属于正常的结构选择。
            if path in HEURISTIC_PATHS and blank_evidence is None:
                blank_evidence = _trace(node, selector, True)
            return
        if value in seen_values:
            return
        seen_values.add(value)
        order += 1
        candidates.append(_make_candidate(path, selector, node, match, order))

    spec = (detail_cfg.get("selectors") or {}).get(field)
    if spec is not None:
        css_selector, _attr, node = _select(soup, spec)
        add(PATH_SELECTOR, css_selector or "", node, "selector")

    if field == "title":
        add(PATH_H1, PATH_H1, soup.find("h1"), "exact")

    kv_match = _find_kv(pairs, field)
    if kv_match is not None:
        label, node, match_kind = kv_match
        add(PATH_KEY_VALUE, f"heuristic:key-value[{label}]", node, match_kind)

    if field == "publisher":
        add(
            PATH_PUBLISHER,
            PATH_PUBLISHER,
            soup.select_one(".publisher, .agency, [rel='author']"),
            "exact",
        )
    if field == "category":
        add(
            PATH_BREADCRUMB,
            PATH_BREADCRUMB,
            soup.select_one(".breadcrumb a:last-of-type, nav a:last-of-type"),
            "partial",
        )
    return candidates, blank_evidence


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
    """抽取单条详情记录，并返回字段级/列表级溯源信息。

    每个字段沿全部证据路径收集候选（``candidates``），再由路径权重与别名
    匹配度打分仲裁出最终采用来源（``adopted``）；``fallback_used`` 只看最终
    采用路径是否为启发式，落选的启发式旁证不会把字段标记为回退。
    """
    soup = BeautifulSoup(html, "html.parser")
    detail_cfg = config.get("detail") or {}
    values: dict[str, str | None] = {}
    traces: dict[str, dict] = {}
    pairs = _key_value_pairs(soup)

    for field in ALL_FIELDS:
        candidates, blank_evidence = _collect_field_candidates(soup, field, detail_cfg, pairs)
        winner = _arbitrate_candidates(candidates, detail_cfg, field)
        if winner is not None:
            values[field] = winner["value"]
            traces[field] = {
                "selector": winner["selector"],
                "fallback": winner["path"] in HEURISTIC_PATHS,
                "location": winner["location"],
                "snippet": winner["snippet"],
                "path": winner["path"],
                "adopted": winner["path"],
                "candidates": candidates,
            }
        else:
            values[field] = None
            if blank_evidence is not None:
                # 回退路径被采用但页面未给出可用值（如空键值单元格）：
                # 无候选可仲裁，仍保留启发式回退证据与 fallback 标记。
                traces[field] = {
                    "selector": blank_evidence["selector"],
                    "fallback": True,
                    "location": blank_evidence["location"],
                    "snippet": blank_evidence["snippet"],
                    "path": blank_evidence["selector"],
                    "adopted": blank_evidence["selector"],
                    "candidates": [],
                }

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
