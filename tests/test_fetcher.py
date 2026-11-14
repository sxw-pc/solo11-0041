import json

import pytest

from app.fetcher import Fetcher, FetchError
from app.identity import derive_item_id, stable_archive_id
from app.repository import find_dataset
from app.store import Store


class FakeResponse:
    def __init__(
        self,
        url: str,
        status_code: int,
        headers: dict | None = None,
        body: bytes = b"",
    ):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.body = body
        self.encoding = "utf-8"
        self.closed = False

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308}

    def iter_content(self, chunk_size: int = 1024):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def _fetcher(tmp_path, session):
    return Fetcher(
        tmp_path / "raw",
        "run_test",
        {"allowed_hosts": ["127.0.0.1", "localhost"]},
        session=session,
    )


def test_external_redirect_is_rejected_before_second_request(tmp_path):
    session = FakeSession([
        FakeResponse(
            "http://127.0.0.1:5000/start",
            302,
            {"Location": "https://example.invalid/outbound"},
        )
    ])
    fetcher = _fetcher(tmp_path, session)
    with pytest.raises(FetchError, match="拒绝非回环目标"):
        fetcher.fetch("http://127.0.0.1:5000/start", "alpha", "detail")
    assert len(session.calls) == 1
    assert session.responses == []
    fetcher.close()


def test_non_loopback_and_credential_urls_never_reach_session(tmp_path):
    session = FakeSession([])
    fetcher = _fetcher(tmp_path, session)
    with pytest.raises(FetchError, match="拒绝非回环目标"):
        fetcher.fetch("https://example.invalid/dataset", "alpha", "detail")
    with pytest.raises(FetchError, match="凭据"):
        fetcher.fetch("http://user:demo@127.0.0.1:5000/dataset", "alpha", "detail")
    assert session.calls == []
    fetcher.close()


DETAIL_BODY = b"<html><body><h1>Shared detail page</h1></body></html>"
FINAL_URL = "http://127.0.0.1:5000/detail/item-42"


def _redirecting_session() -> FakeSession:
    return FakeSession(
        [
            FakeResponse(
                "http://127.0.0.1:5000/entry/one", 302, {"Location": FINAL_URL}
            ),
            FakeResponse(
                FINAL_URL,
                200,
                {"Content-Type": "text/html; charset=utf-8"},
                body=DETAIL_BODY,
            ),
            FakeResponse(
                "http://127.0.0.1:5000/entry/two", 307, {"Location": FINAL_URL}
            ),
            FakeResponse(
                FINAL_URL,
                200,
                {"Content-Type": "text/html; charset=utf-8"},
                body=DETAIL_BODY,
            ),
        ]
    )


def test_redirected_entry_urls_share_one_file_and_one_manifest_entry(tmp_path):
    archived = []
    fetcher = Fetcher(
        tmp_path / "raw",
        "run_test",
        {"allowed_hosts": ["127.0.0.1", "localhost"]},
        session=_redirecting_session(),
        archive_callback=archived.append,
    )
    first = fetcher.fetch(
        "http://127.0.0.1:5000/entry/one", "alpha", "detail", item_id="one"
    )
    second = fetcher.fetch(
        "http://127.0.0.1:5000/entry/two", "alpha", "detail", item_id="two"
    )
    stable_id = stable_archive_id("alpha", "detail", "item-42")
    assert first.item_id == second.item_id == "item-42"
    assert first.stable_id == second.stable_id == stable_id
    assert first.file_path == second.file_path
    assert first.sha256 == second.sha256
    # 回调仍在每次成功抓取后触发，行为不变。
    assert [page.stable_id for page in archived] == [stable_id, stable_id]
    fetcher.close()

    manifest = json.loads(
        (tmp_path / "raw" / "run_test" / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest["entries"]) == 1
    entry = manifest["entries"][0]
    assert entry["item_id"] == "item-42"
    assert entry["stable_id"] == stable_id
    assert entry["final_url"] == FINAL_URL
    assert entry["kind"] == "detail"
    assert entry["sha256"] == first.sha256
    assert (tmp_path / "raw" / "run_test" / entry["file"]).read_bytes() == DETAIL_BODY


def test_plain_non_redirect_fetch_derives_item_id_from_path(tmp_path):
    session = FakeSession([
        FakeResponse(
            "http://127.0.0.1:5000/dataset/plain",
            200,
            {"Content-Type": "text/html; charset=utf-8"},
            body=b"<html>plain</html>",
        )
    ])
    fetcher = _fetcher(tmp_path, session)
    page = fetcher.fetch("http://127.0.0.1:5000/dataset/plain", "alpha", "detail")
    assert page.item_id == "plain"
    assert page.stable_id == stable_archive_id("alpha", "detail", "plain")
    assert len(session.calls) == 1
    fetcher.close()


def test_list_page_without_item_id_falls_back_to_stable_url_derived_key(tmp_path):
    body = b"<html><body>list</body></html>"
    session = FakeSession([
        FakeResponse(
            "http://127.0.0.1:5000/catalog?page=1",
            200,
            {"Content-Type": "text/html; charset=utf-8"},
            body=body,
        ),
        FakeResponse(
            "http://127.0.0.1:5000/catalog?page=1",
            200,
            {"Content-Type": "text/html; charset=utf-8"},
            body=body,
        ),
    ])
    fetcher = _fetcher(tmp_path, session)
    first = fetcher.fetch("http://127.0.0.1:5000/catalog?page=1", "alpha", "list")
    second = fetcher.fetch("http://127.0.0.1:5000/catalog?page=1", "alpha", "list")
    expected_id = derive_item_id("http://127.0.0.1:5000/catalog?page=1", "list")
    assert first.item_id == second.item_id == expected_id
    assert first.stable_id == second.stable_id
    assert first.file_path == second.file_path
    fetcher.close()
    manifest = json.loads(
        (tmp_path / "raw" / "run_test" / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest["entries"]) == 1


def _group_with_instances(instances: list[dict]) -> dict:
    return {
        "dataset_uid": "ds_redirect_merge",
        "title": "Shared Detail",
        "title_norm": "shared detail",
        "category": "environment",
        "publisher": "Atlas Environmental Agency",
        "publisher_norm": "atlas environmental agency",
        "temporal_start": "2023-01-01",
        "temporal_end": "2023-12-31",
        "geography": "US",
        "license": "CC-BY-4.0",
        "update_frequency": "daily",
        "fields": [],
        "resources": [],
        "instances": instances,
        "source_count": 1,
        "fingerprint": "fp_redirect_merge",
        "findings": [],
    }


def test_store_upserts_redirect_duplicates_and_provenance_hits_stable_key(tmp_path):
    db_path = tmp_path / "archive.db"
    store = Store(db_path)
    instances = []
    try:
        fetcher = Fetcher(
            tmp_path / "raw",
            "run_test",
            {"allowed_hosts": ["127.0.0.1", "localhost"]},
            session=_redirecting_session(),
            archive_callback=lambda page: store.archive_raw(
                "run_test", page.source, page.url, page.kind, page.file_path,
                page.status_code, page.byte_count, page.fetched_at, page.sha256,
                page.content_type, page.item_id,
            ),
        )
        for entry_url in (
            "http://127.0.0.1:5000/entry/one",
            "http://127.0.0.1:5000/entry/two",
        ):
            page = fetcher.fetch(entry_url, "alpha", "detail", item_id=entry_url.rsplit("/", 1)[-1])
            instances.append(
                {
                    "instance_id": f"inst_{page.item_id}_{entry_url.rsplit('/', 1)[-1]}",
                    "source": "alpha",
                    "source_url": entry_url,
                    "source_item_id": page.item_id,
                    "title": "Shared Detail",
                    "category": "environment",
                    "publisher": "Atlas Environmental Agency",
                    "temporal_start": "2023-01-01",
                    "temporal_end": "2023-12-31",
                    "geography": "US",
                    "license": "CC-BY-4.0",
                    "update_frequency": "daily",
                    "fields": [],
                    "resources": [],
                    "extraction": {},
                    "transforms": [],
                    "findings": [],
                }
            )
        fetcher.close()

        rows = store.conn.execute("SELECT url, item_id, stable_id FROM raw_archive").fetchall()
        assert len(rows) == 1
        assert rows[0]["item_id"] == "item-42"
        assert rows[0]["stable_id"] == stable_archive_id("alpha", "detail", "item-42")

        store.persist_groups("run_test", [_group_with_instances(instances)])
    finally:
        store.close()

    dataset = find_dataset(str(db_path), "ds_redirect_merge", "run_test")
    provenance = dataset["provenance"]
    assert len(provenance) == 2
    raw_rows = [item["raw_archive"] for item in provenance]
    assert all(raw is not None for raw in raw_rows)
    assert raw_rows[0]["sha256"] == raw_rows[1]["sha256"]
    assert raw_rows[0]["file_path"] == raw_rows[1]["file_path"]
    # 两个入口 URL 都能命中同一行留档，不再有孤儿记录。
    assert {item["url"] for item in provenance} == {
        "http://127.0.0.1:5000/entry/one",
        "http://127.0.0.1:5000/entry/two",
    }
    assert {item["stable_id"] for item in provenance} == {
        stable_archive_id("alpha", "detail", "item-42")
    }
