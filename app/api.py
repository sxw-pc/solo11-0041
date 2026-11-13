"""本地控制 API：触发采集、查询运行与数据集、即时导出。"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

from .export import export_one
from .pipeline import run_pipeline
from .repository import get_run, list_datasets

bp = Blueprint("control_api", __name__)


def _settings() -> tuple[str, str, str]:
    return (
        str(current_app.config["DB_PATH"]),
        str(current_app.config["DATA_DIR"]),
        str(current_app.config["CONFIG_DIR"]),
    )


@bp.post("/runs")
def create_run():
    payload = request.get_json(silent=True) or {}
    source = payload.get("source", "all")
    if not isinstance(source, (str, list)):
        return jsonify({"error": "source must be a string or list"}), 400
    db_path, data_dir, config_dir = _settings()
    replay_from = payload.get("replay_from")
    try:
        result = run_pipeline(
            source,
            replay_from=replay_from,
            db_path=db_path,
            data_dir=data_dir,
            config_dir=config_dir,
            base_url=request.host_url.rstrip("/"),
        )
    except Exception as exc:  # 本地控制接口将失败显式返回
        return jsonify({"error": str(exc)}), 500
    return jsonify(result), 201


@bp.get("/runs/<run_id>")
def read_run(run_id: str):
    db_path, _, _ = _settings()
    result = get_run(db_path, run_id)
    if result is None:
        return jsonify({"error": "run not found"}), 404
    return jsonify(result)


@bp.get("/datasets")
def read_datasets():
    db_path, _, _ = _settings()
    try:
        limit = int(request.args.get("limit", 200))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    result = list_datasets(
        db_path,
        category=request.args.get("category"),
        publisher=request.args.get("publisher"),
        temporal_from=request.args.get("temporal_from"),
        temporal_to=request.args.get("temporal_to"),
        updated_from=request.args.get("updated_from"),
        updated_to=request.args.get("updated_to"),
        severity=request.args.get("severity"),
        limit=limit,
    )
    return jsonify({"count": len(result), "datasets": result})


@bp.get("/export")
def read_export():
    fmt = (request.args.get("format") or "json").lower()
    if fmt not in {"json", "md"}:
        return jsonify({"error": "format must be json or md"}), 400
    db_path, data_dir, _ = _settings()
    run_id = request.args.get("run_id")
    mimetype, body = export_one(db_path, fmt, Path(data_dir) / "exports" / "api", run_id)
    return Response(body, mimetype=mimetype)
