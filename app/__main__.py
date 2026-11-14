"""命令行入口：serve / run / export / inspect。"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from werkzeug.serving import make_server

from .export import export_archive
from .pipeline import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_DATA_DIR,
    DEFAULT_DB_PATH,
    load_sources,
    run_pipeline,
    selected_source_names,
)
from .repository import list_datasets
from .server import create_app


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 档案路径")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="原始留档与导出目录")
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR), help="YAML 配置目录")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app", description="采谱仪 · 数据集谱系档案管道")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动本地 mock 门户与 API（仅 127.0.0.1）")
    serve.add_argument("--port", type=int, default=5000)
    _common(serve)

    run = sub.add_parser("run", help="运行一次采集管道")
    run.add_argument("--source", default="all", help="all、单个源，或逗号分隔的源名")
    run.add_argument("--replay-from", help="从 raw 留档目录或 fixtures 目录重放，不发起 HTTP")
    _common(run)

    export = sub.add_parser("export", help="导出 JSON / Markdown 档案")
    export.add_argument("--format", choices=["json", "md"], default="md")
    export.add_argument("--run-id", help="仅导出该 run 的数据集")
    export.add_argument("--out", help="输出目录")
    _common(export)

    inspect = sub.add_parser("inspect", help="只读查询数据集")
    inspect.add_argument("--category")
    inspect.add_argument("--publisher")
    inspect.add_argument("--updated-from", help="dataset.updated_at 下界，ISO 时间")
    inspect.add_argument("--updated-to", help="dataset.updated_at 上界，ISO 时间")
    inspect.add_argument("--severity", choices=["error", "warn", "info"])
    inspect.add_argument("--limit", type=int, default=50)
    _common(inspect)
    return parser


def _server_ready(base_url: str) -> bool:
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(base_url.rstrip("/") + "/health", timeout=0.5)
        return response.status_code == 200 and response.json().get("status") == "ok"
    except (requests.RequestException, ValueError):
        return False
    finally:
        session.close()


def _start_embedded(base_url: str, db_path: str, data_dir: str, config_dir: str):
    parts = urlsplit(base_url)
    host, port = parts.hostname or "127.0.0.1", parts.port or 80
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("mock 门户只能通过回环地址启动")
    app = create_app(db_path=db_path, data_dir=data_dir, config_dir=config_dir)
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, name="mock-portal", daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if _server_ready(base_url):
            return server, thread
        time.sleep(0.05)
    server.shutdown()
    raise RuntimeError(f"本地 mock 门户未能在 {base_url} 启动")


def command_serve(args: argparse.Namespace) -> int:
    app = create_app(db_path=args.db, data_dir=args.data_dir, config_dir=args.config_dir)
    print(f"datasheet-probe listening on http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=True)
    return 0


def _run_without_replay(args: argparse.Namespace) -> dict:
    config = load_sources(args.config_dir)
    selected = selected_source_names(config, args.source)
    netlocs = {urlsplit(config["sources"][name]["base_url"]).netloc for name in selected}
    if len(netlocs) != 1:
        raise ValueError("一次 CLI 运行仅支持共享同一回环地址的数据源；请先运行 serve")
    base_url = config["sources"][selected[0]]["base_url"].rstrip("/")
    embedded = None
    thread = None
    if not _server_ready(base_url):
        embedded, thread = _start_embedded(base_url, args.db, args.data_dir, args.config_dir)
        print(f"embedded mock portal: {base_url}")
    try:
        return run_pipeline(
            args.source, config_dir=args.config_dir, data_dir=args.data_dir,
            db_path=args.db, base_url=base_url,
        )
    finally:
        if embedded is not None:
            embedded.shutdown()
            if thread is not None:
                thread.join(timeout=2)


def command_run(args: argparse.Namespace) -> int:
    try:
        result = run_pipeline(
            args.source,
            replay_from=args.replay_from,
            config_dir=args.config_dir,
            data_dir=args.data_dir,
            db_path=args.db,
        ) if args.replay_from else _run_without_replay(args)
    except Exception as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: result[key] for key in ("run_id", "counts", "raw_pages", "raw_failures", "errors", "export_dir")},
                     ensure_ascii=False, indent=2))
    counts = result["counts"]
    print(
        f"summary: new={counts['new']} updated={counts['updated']} "
        f"unchanged={counts['unchanged']} merged={counts['merged']} "
        f"raw_pages={result['raw_pages']} raw_failures={result['raw_failures']}"
    )
    return 0 if counts.get("datasets", 0) > 0 else 2


def command_export(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 2
    output = Path(args.out) if args.out else Path(args.data_dir) / "exports" / "manual"
    try:
        result = export_archive(str(db_path), output, args.run_id)
    except Exception as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 2
    key = "md" if args.format == "md" else "json"
    print(json.dumps({"format": args.format, "path": result[key], "stats": result["archive"]["stats"]},
                     ensure_ascii=False, indent=2))
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    if not Path(args.db).exists():
        print(f"database not found: {args.db}", file=sys.stderr)
        return 2
    rows = list_datasets(
        args.db, category=args.category, publisher=args.publisher,
        updated_from=args.updated_from, updated_to=args.updated_to,
        severity=args.severity, limit=args.limit,
    )
    print(json.dumps({"count": len(rows), "datasets": rows}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        return command_serve(args)
    if args.command == "run":
        return command_run(args)
    if args.command == "export":
        return command_export(args)
    if args.command == "inspect":
        return command_inspect(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
