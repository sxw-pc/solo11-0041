"""采集管道编排：发现 → 抓取留档 → 抽取 → 归一 → 校验 → 合并 → 入库 → 导出。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .dedupe import group_instances, instance_id
from .discover import discover_source
from .export import export_archive
from .extract import extract_detail
from .fetcher import Fetcher
from .normalize import load_rules, normalize_record
from .store import Store
from .validate import validate_cross_source, validate_record

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "datasheet_probe.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


def load_sources(config_dir: str | Path, base_url: str | None = None) -> dict:
    path = Path(config_dir) / "sources.yaml"
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if base_url:
        for source in (config.get("sources") or {}).values():
            source["base_url"] = base_url.rstrip("/")
    return config


def selected_source_names(config: dict, requested: str | list[str] | None) -> list[str]:
    available = list((config.get("sources") or {}).keys())
    if requested is None or requested == "all" or requested == ["all"]:
        return available
    names = requested.split(",") if isinstance(requested, str) else list(requested)
    names = [name.strip() for name in names if name and name.strip() and name != "all"]
    if not names:
        return available
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"未知数据源: {', '.join(unknown)}; 可选: {', '.join(available)}")
    return names


def _replay_entries(replay_from: str | Path) -> list[dict]:
    root = Path(replay_from)
    if not root.exists():
        raise FileNotFoundError(f"replay 目录不存在: {root}")
    manifests = [root / "manifest.json"] if (root / "manifest.json").exists() else sorted(root.glob("*/manifest.json"))
    if not manifests:
        raise FileNotFoundError(f"未找到 manifest.json: {root}")
    entries: list[dict] = []
    for manifest_path in manifests:
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        fallback_source = manifest.get("source")
        for entry in manifest.get("entries") or []:
            item = dict(entry)
            item["source"] = item.get("source") or fallback_source
            file_path = Path(str(item.get("file") or ""))
            item["path"] = file_path if file_path.is_absolute() else manifest_path.parent / file_path
            entries.append(item)
    return entries


def _normalize_instances(raw_records: list[dict], rules: dict) -> list[dict]:
    instances: list[dict] = []
    seen: set[str] = set()
    for record in raw_records:
        normalized, transforms = normalize_record(record, rules)
        normalized["instance_id"] = instance_id(normalized["source"], normalized["source_url"])
        if normalized["instance_id"] in seen:
            continue
        seen.add(normalized["instance_id"])
        normalized["transforms"] = transforms
        normalized["findings"] = validate_record(normalized, rules)
        instances.append(normalized)
    return instances


def _run_http_sources(
    config: dict,
    selected: list[str],
    store: Store,
    run_id: str,
    raw_root: Path,
) -> tuple[list[dict], int, list[str], dict[str, dict]]:
    raw_records: list[dict] = []
    errors: list[str] = []
    run_info: dict[str, dict] = {}
    raw_pages = 0
    defaults = config.get("defaults") or {}

    def callback(page) -> None:
        nonlocal raw_pages
        store.archive_raw(
            run_id, page.source, page.url, page.kind, page.file_path, page.status_code,
            page.byte_count, page.fetched_at, page.sha256, page.content_type,
        )
        raw_pages += 1

    fetcher = Fetcher(
        raw_root, run_id, defaults,
        source_configs=config.get("sources") or {}, archive_callback=callback,
    )
    try:
        for source in selected:
            cfg = (config.get("sources") or {})[source]
            started = _now()
            clock = time.perf_counter()
            pages = discovered = 0
            termination: str | None = None
            source_errors: list[str] = []
            try:
                result = discover_source(fetcher.fetch, source, cfg)
                pages, discovered = result.page_count, result.discovered
                termination = result.termination.value if result.termination else None
                for entry in result.entries:
                    try:
                        page = fetcher.fetch(entry["url"], source, "detail")
                        record = extract_detail(page.html, entry["url"], source, cfg)
                        record["source_item_id"] = entry.get("item_id")
                        raw_records.append(record)
                    except Exception as exc:  # 单页失败不终止整个数据源
                        source_errors.append(f"{entry.get('url')}: {exc}")
                status = "partial" if source_errors else "ok"
            except Exception as exc:
                source_errors.append(str(exc))
                status = "error"
            elapsed = int((time.perf_counter() - clock) * 1000)
            error_text = "; ".join(source_errors[:5]) or None
            store.record_run(
                run_id, source, "http", status, started, _now(), pages, discovered, elapsed, error_text
            )
            errors.extend(f"{source}: {message}" for message in source_errors)
            run_info[source] = {
                "status": status, "pages": pages, "discovered": discovered,
                "termination": termination,
                "extracted": len([r for r in raw_records if r.get("source") == source]),
                "elapsed_ms": elapsed, "errors": source_errors,
            }
    finally:
        fetcher.close()
    return raw_records, raw_pages, errors, run_info


def _run_replay_sources(
    config: dict,
    selected: list[str],
    store: Store,
    run_id: str,
    entries: list[dict],
    rules: dict,
) -> tuple[list[dict], int, list[str], dict[str, dict]]:
    raw_records: list[dict] = []
    errors: list[str] = []
    run_info: dict[str, dict] = {}
    raw_pages = 0
    by_source: dict[str, list[dict]] = {name: [] for name in selected}
    for entry in entries:
        if entry.get("source") in by_source:
            by_source[entry["source"]].append(entry)
    for source in selected:
        started = _now()
        clock = time.perf_counter()
        source_entries = by_source.get(source, [])
        pages = discovered = 0
        source_errors: list[str] = []
        for entry in source_entries:
            path = Path(entry["path"])
            kind = str(entry.get("kind") or "detail")
            if kind == "list":
                pages += 1
            else:
                discovered += 1
            if not path.exists():
                source_errors.append(f"缺少 replay 文件 {path}")
                continue
            body = path.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if entry.get("sha256") and entry["sha256"] != digest:
                source_errors.append(f"sha256 不匹配 {path}")
                continue
            store.archive_raw(
                run_id, source, str(entry.get("url")), kind, str(path),
                int(entry.get("status_code", 200)), len(body),
                str(entry.get("fetched_at") or _now()), digest,
                entry.get("content_type", "text/html"),
            )
            raw_pages += 1
            if kind == "list":
                continue
            html = body.decode("utf-8", errors="replace")
            try:
                record = extract_detail(html, str(entry.get("url")), source, config["sources"][source])
                record["source_item_id"] = entry.get("item_id")
                raw_records.append(record)
            except Exception as exc:
                source_errors.append(f"{entry.get('url')}: {exc}")
        status = "partial" if source_errors else "ok"
        elapsed = int((time.perf_counter() - clock) * 1000)
        store.record_run(
            run_id, source, "replay", status, started, _now(), pages, discovered, elapsed,
            "; ".join(source_errors[:5]) or None,
        )
        errors.extend(f"{source}: {message}" for message in source_errors)
        run_info[source] = {
            "status": status, "pages": pages, "discovered": discovered,
            "termination": None,  # replay 按 manifest 重放，不经过翻页状态机
            "extracted": len([r for r in raw_records if r.get("source") == source]),
            "elapsed_ms": elapsed, "errors": source_errors,
        }
    return raw_records, raw_pages, errors, run_info


def run_pipeline(
    source: str | list[str] | None = None,
    *,
    replay_from: str | Path | None = None,
    config_dir: str | Path = DEFAULT_CONFIG_DIR,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    db_path: str | Path | None = None,
    base_url: str | None = None,
    export_after: bool = True,
    run_id: str | None = None,
) -> dict:
    """运行一次完整管道，返回四项幂等计数与导出摘要。"""
    data_path = Path(data_dir)
    config = load_sources(config_dir, base_url)
    selected = selected_source_names(config, source)
    rules = load_rules(str(Path(config_dir) / "normalize_rules.yaml"))
    run_id = run_id or new_run_id()
    store = Store(db_path or DEFAULT_DB_PATH)
    try:
        if replay_from:
            entries = _replay_entries(replay_from)
            raw_records, raw_pages, errors, run_info = _run_replay_sources(
                config, selected, store, run_id, entries, rules
            )
        else:
            raw_records, raw_pages, errors, run_info = _run_http_sources(
                config, selected, store, run_id, data_path / "raw"
            )
        instances = _normalize_instances(raw_records, rules)
        groups = group_instances(instances, rules.get("dedupe") or {})
        for group in groups:
            group["findings"] = validate_cross_source(group["instances"], rules)
        groups.sort(key=lambda item: item["dataset_uid"])
        counts = store.persist_groups(run_id, groups) if groups else {
            "new": 0, "updated": 0, "unchanged": 0, "merged": 0,
            "datasets": 0, "instances": 0, "findings": 0, "transforms": 0,
        }
    finally:
        store.close()
    export_dir = None
    stats = None
    if export_after:
        export_dir = data_path / "exports" / run_id
        result = export_archive(str(db_path or DEFAULT_DB_PATH), str(export_dir), run_id)
        stats = result["archive"]["stats"]
    return {
        "run_id": run_id,
        "counts": counts,
        "raw_pages": raw_pages,
        "errors": errors,
        "sources": run_info,
        "export_dir": str(export_dir) if export_dir else None,
        "stats": stats,
    }
