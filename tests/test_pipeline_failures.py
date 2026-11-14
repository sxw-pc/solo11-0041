"""失败留档与成功页计数分离的管道级证据：注入 FakeSession 让某详情页 503 至耗尽。"""

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import yaml

from app.pipeline import run_pipeline
from app.repository import get_run

ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG_DIR = ROOT / "config"
FIXTURES_DIR = ROOT / "fixtures"

FAILED_DETAIL_URL = "http://127.0.0.1:5000/alpha/dataset/metro-transit-ridership"
OK_DETAIL_URL = "http://127.0.0.1:5000/alpha/dataset/air-quality-2023"
BODY_503 = b"<html><head><title>503</title></head><body>upstream unavailable</body></html>"


class FakeResponse:
    def __init__(self, url: str, status_code: int, body: bytes = b""):
        self.url = url
        self.status_code = status_code
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self.body = body
        self.encoding = None

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308}

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def close(self) -> None:
        pass


class RouterSession:
    """按 URL 路由的伪 Session：每路 URL 依次弹出预置响应。"""

    def __init__(self, routes: dict[str, list[FakeResponse]]):
        self.routes = {url: list(items) for url, items in routes.items()}
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        return self.routes[url].pop(0)

    def close(self) -> None:
        pass


def _alpha_config_dir(tmp_path: Path) -> Path:
    """真实 config 的 defaults+alpha 子集：重试等待与节流改为可观察的确定值。"""
    config = yaml.safe_load((REAL_CONFIG_DIR / "sources.yaml").read_text(encoding="utf-8"))
    defaults = config["defaults"]
    defaults["max_retries"] = 2
    defaults["retry_backoff_seconds"] = 0.5
    defaults["min_interval_seconds"] = 0
    alpha = config["sources"]["alpha"]
    alpha["min_interval_seconds"] = 0
    target = tmp_path / "config"
    target.mkdir()
    (target / "sources.yaml").write_text(
        yaml.safe_dump({"version": 1, "defaults": defaults, "sources": {"alpha": alpha}},
                       allow_unicode=True),
        encoding="utf-8",
    )
    shutil.copy(REAL_CONFIG_DIR / "normalize_rules.yaml", target / "normalize_rules.yaml")
    return target


def _alpha_routes() -> dict[str, list[FakeResponse]]:
    manifest = json.loads((FIXTURES_DIR / "portal_alpha" / "manifest.json").read_text("utf-8"))
    routes: dict[str, list[FakeResponse]] = {}
    for entry in manifest["entries"]:
        url = entry["url"]
        if url == FAILED_DETAIL_URL:
            routes[url] = [FakeResponse(url, 503, BODY_503) for _ in range(3)]
        else:
            body = (FIXTURES_DIR / "portal_alpha" / entry["file"]).read_bytes()
            routes[url] = [FakeResponse(url, 200, body)]
    return routes


def test_exhausted_503_is_archived_as_failed_without_inflating_success_counts(tmp_path):
    config_dir = _alpha_config_dir(tmp_path)
    session = RouterSession(_alpha_routes())
    sleeps: list[float] = []
    db_path = tmp_path / "archive.db"
    data_dir = tmp_path / "data"

    result = run_pipeline(
        "alpha",
        config_dir=config_dir,
        db_path=db_path,
        data_dir=data_dir,
        session=session,
        sleep_fn=sleeps.append,
    )

    # 失败详情页 1+max_retries 次尝试；重试等待经 sleep_fn 观察且有硬上限，不叠加成长睡眠
    assert session.calls.count(FAILED_DETAIL_URL) == 3
    assert sleeps == [0.5, 1.0]
    assert sum(sleeps) <= 3 * 1.0

    # 计数口径分离：6 个成功页（2 列表 + 4 详情）+ 1 个失败留档
    assert result["raw_pages"] == 6
    assert result["raw_failures"] == 1
    assert result["counts"]["datasets"] == 4
    assert result["counts"]["instances"] == 4
    assert result["sources"]["alpha"]["status"] == "partial"
    assert result["sources"]["alpha"]["failed_pages"] == 1
    assert any(FAILED_DETAIL_URL in message for message in result["errors"])

    # raw 目录与 manifest 同时保留失败证据，且带可区分语义
    raw_dir = data_dir / "raw" / result["run_id"]
    manifest = json.loads((raw_dir / "manifest.json").read_text("utf-8"))
    entries = {entry["url"]: entry for entry in manifest["entries"]}
    assert len(entries) == 7
    failed_entry = entries[FAILED_DETAIL_URL]
    assert failed_entry["outcome"] == "failed"
    assert failed_entry["status_code"] == 503
    failed_file = raw_dir / failed_entry["file"]
    assert failed_file.read_bytes() == BODY_503
    ok_entry = entries[OK_DETAIL_URL]
    assert ok_entry["outcome"] == "ok"
    assert ok_entry["status_code"] == 200

    # SQLite 留档：失败行与成功行可区分，成功页不受污染
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT url, status_code, outcome, sha256 FROM raw_archive WHERE run_id=?",
            (result["run_id"],),
        ).fetchall()
        assert len(rows) == 7
        by_url = {row["url"]: row for row in rows}
        failed = by_url[FAILED_DETAIL_URL]
        assert failed["outcome"] == "failed"
        assert failed["status_code"] == 503
        assert failed["sha256"] == hashlib.sha256(BODY_503).hexdigest()
        ok_rows = [row for row in rows if row["outcome"] == "ok"]
        assert len(ok_rows) == 6
        assert by_url[OK_DETAIL_URL]["status_code"] == 200
        # 另一路成功页照常抽取入库，未被失败留档污染
        titles = {
            row[0] for row in conn.execute("SELECT title FROM dataset").fetchall()
        }
        assert "Air Quality Monitoring Stations 2023" in titles
        assert len(titles) == 4
    finally:
        conn.close()

    # 查询侧与导出侧计数同样区分成功页与失败响应
    run = get_run(str(db_path), result["run_id"])
    assert run["counts"]["raw_pages"] == 6
    assert run["counts"]["raw_failures"] == 1
    stats = result["stats"]
    assert stats["raw_archive_count"] == 6
    assert stats["raw_failure_count"] == 1
    markdown = (Path(result["export_dir"]) / "archive.md").read_text(encoding="utf-8")
    assert "| 原始留档页面 | 6 |" in markdown
    assert "| 失败留档响应 | 1 |" in markdown


def test_replay_manifest_failed_entry_is_preserved_but_not_counted_or_extracted(
    tmp_path, config_dir
):
    replay_dir = tmp_path / "replay_alpha"
    shutil.copytree(FIXTURES_DIR / "portal_alpha", replay_dir)
    broken_body = b"<html><body>503 service unavailable</body></html>"
    (replay_dir / "detail-broken-503.html").write_bytes(broken_body)
    manifest_path = replay_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    # 旧式 manifest 没有 outcome 字段：由 status_code 推导失败语义
    manifest["entries"].append(
        {
            "source": "alpha",
            "url": "http://127.0.0.1:5000/alpha/dataset/broken-503",
            "kind": "detail",
            "item_id": "broken-503",
            "file": "detail-broken-503.html",
            "status_code": 503,
            "byte_count": len(broken_body),
            "sha256": hashlib.sha256(broken_body).hexdigest(),
            "content_type": "text/html; charset=utf-8",
            "fetched_at": "2026-11-13T01:54:13+00:00",
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    db_path = tmp_path / "replay.db"
    result = run_pipeline(
        "alpha",
        replay_from=replay_dir,
        config_dir=config_dir,
        db_path=db_path,
        data_dir=tmp_path / "data",
    )

    assert result["errors"] == []
    assert result["raw_pages"] == 7  # 2 列表 + 5 成功详情
    assert result["raw_failures"] == 1
    assert result["counts"]["datasets"] == 5  # 失败响应不参与抽取
    assert result["sources"]["alpha"]["failed_pages"] == 1
    assert result["sources"]["alpha"]["status"] == "ok"
    assert result["stats"]["raw_archive_count"] == 7
    assert result["stats"]["raw_failure_count"] == 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status_code, outcome, sha256 FROM raw_archive WHERE url=?",
            ("http://127.0.0.1:5000/alpha/dataset/broken-503",),
        ).fetchone()
        assert tuple(row) == (503, "failed", hashlib.sha256(broken_body).hexdigest())
        ok_count = conn.execute(
            "SELECT COUNT(*) FROM raw_archive WHERE run_id=? AND outcome='ok'",
            (result["run_id"],),
        ).fetchone()[0]
        assert ok_count == 7
    finally:
        conn.close()
