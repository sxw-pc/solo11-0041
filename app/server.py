"""Flask 装配：mock 门户 + 本地控制 API，仅允许回环访问。"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, jsonify, request

from .api import bp as api_bp
from .mock_portal import bp as portal_bp
from .store import Store

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_app(
    db_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DB_PATH=str(db_path or PROJECT_ROOT / "data" / "datasheet_probe.db"),
        DATA_DIR=str(data_dir or PROJECT_ROOT / "data"),
        CONFIG_DIR=str(config_dir or PROJECT_ROOT / "config"),
        JSON_AS_ASCII=False,
    )
    app.register_blueprint(portal_bp)
    app.register_blueprint(api_bp)

    # 首次启动也保证空库具备完整 schema，查询接口返回空集合而非 500。
    store = Store(app.config["DB_PATH"])
    store.close()

    @app.before_request
    def loopback_only():
        # 即使误绑定到外部接口，也拒绝非回环调用方。
        if request.remote_addr not in {"127.0.0.1", "::1", None}:
            abort(403)
        return None

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "datasheet-probe", "mode": "offline-mock"})

    return app
