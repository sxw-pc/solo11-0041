"""从内置 mock 门户生成 fixtures/ 静态离线包，便于 replay 测试。"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.discover import discover_source  # noqa: E402
from app.pipeline import load_sources  # noqa: E402
from app.server import create_app  # noqa: E402


@dataclass
class Page:
    html: str
    url: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> None:
    app = create_app(db_path=ROOT / "data" / "fixture_build.db", data_dir=ROOT / "data", config_dir=ROOT / "config")
    client = app.test_client()
    config = load_sources(ROOT / "config")

    def fetch(url: str, source: str, kind: str) -> Page:
        del source, kind
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        response = client.get(path)
        if response.status_code != 200:
            raise RuntimeError(f"{path} -> {response.status_code}")
        return Page(html=response.get_data(as_text=True), url=url)

    for source, cfg in config["sources"].items():
        target = ROOT / "fixtures" / f"portal_{source}"
        target.mkdir(parents=True, exist_ok=True)
        for old in target.glob("*.html"):
            old.unlink()
        discovery = discover_source(fetch, source, cfg)
        entries: list[dict] = []
        for index, page in enumerate(discovery.pages, start=1):
            filename = f"list-{index}.html"
            body = page.html.encode("utf-8")
            (target / filename).write_bytes(body)
            entries.append(
                {
                    "source": source,
                    "url": getattr(page, "url", f"{cfg['base_url']}{cfg['start_path']}?page={index}"),
                    "kind": "list",
                    "file": filename,
                    "status_code": 200,
                    "byte_count": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "content_type": "text/html; charset=utf-8",
                    "fetched_at": _now(),
                }
            )
        for entry in discovery.entries:
            page = fetch(entry["url"], source, "detail")
            filename = f"detail-{entry['item_id']}.html"
            body = page.html.encode("utf-8")
            (target / filename).write_bytes(body)
            entries.append(
                {
                    "source": source,
                    "url": entry["url"],
                    "kind": "detail",
                    "item_id": entry["item_id"],
                    "file": filename,
                    "status_code": 200,
                    "byte_count": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "content_type": "text/html; charset=utf-8",
                    "fetched_at": _now(),
                }
            )
        manifest = {
            "source": source,
            "base_url": cfg["base_url"],
            "generated_at": _now(),
            "entries": entries,
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"{source}: {len(discovery.pages)} list pages, {len(discovery.entries)} detail pages")


if __name__ == "__main__":
    main()
