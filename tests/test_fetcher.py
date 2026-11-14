import json
from pathlib import Path

import pytest
import requests

from app.fetcher import RETRY_BACKOFF_CAP, Fetcher, FetchError


class FakeResponse:
    def __init__(self, url: str, status_code: int, headers: dict | None = None,
                 body: bytes = b""):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.body = body
        self.encoding = None
        self.closed = False

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308}

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

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


class RouterSession:
    """按 URL 路由的伪会话：每路 URL 依次弹出响应或抛出异常。"""

    def __init__(self, routes: dict[str, list]):
        self.routes = {url: list(items) for url, items in routes.items()}
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        item = self.routes[url].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

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


def _failure_fetcher(tmp_path, session, *, max_retries=2, backoff=0.5):
    sleeps: list[float] = []
    archived: list = []
    fetcher = Fetcher(
        tmp_path / "raw",
        "run_test",
        {
            "allowed_hosts": ["127.0.0.1", "localhost"],
            "max_retries": max_retries,
            "retry_backoff_seconds": backoff,
            "min_interval_seconds": 0,
        },
        session=session,
        sleep_fn=sleeps.append,
        archive_callback=archived.append,
    )
    return fetcher, sleeps, archived


def _manifest(tmp_path) -> dict:
    path = tmp_path / "raw" / "run_test" / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_5xx_exhaustion_archives_failed_response_and_sleeps_via_sleep_fn(tmp_path):
    url = "http://127.0.0.1:5000/alpha/dataset/broken"
    body = b"<html><body>503 upstream</body></html>"
    session = RouterSession({url: [FakeResponse(url, 503, body=body) for _ in range(3)]})
    fetcher, sleeps, archived = _failure_fetcher(tmp_path, session, max_retries=2, backoff=0.5)

    with pytest.raises(FetchError, match="HTTP 503"):
        fetcher.fetch(url, "alpha", "detail")
    fetcher.close()

    assert session.calls == [url] * 3  # 1 + max_retries 次尝试
    # 重试等待全部经注入的 sleep_fn 观察，且被硬上限截断，不叠加成长睡眠
    assert sleeps == [0.5, 1.0]
    assert all(0 < item <= RETRY_BACKOFF_CAP for item in sleeps)
    assert sum(sleeps) <= 2 * RETRY_BACKOFF_CAP

    assert len(archived) == 1
    page = archived[0]
    assert page.outcome == "failed"
    assert page.status_code == 503
    assert Path(page.file_path).read_bytes() == body

    entries = _manifest(tmp_path)["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["url"] == url
    assert entry["status_code"] == 503
    assert entry["outcome"] == "failed"


def test_connection_error_exhaustion_archives_failed_placeholder(tmp_path):
    url = "http://127.0.0.1:5000/alpha/dataset/unreachable"
    session = RouterSession({url: [requests.ConnectionError("refused") for _ in range(2)]})
    fetcher, sleeps, archived = _failure_fetcher(tmp_path, session, max_retries=1, backoff=0.5)

    with pytest.raises(FetchError, match="请求失败"):
        fetcher.fetch(url, "alpha", "detail")
    fetcher.close()

    assert session.calls == [url] * 2
    assert sleeps == [0.5]
    assert len(archived) == 1
    page = archived[0]
    assert page.outcome == "failed"
    assert page.status_code == 0  # 无响应可存，占位记录
    assert "refused" in Path(page.file_path).read_text(encoding="utf-8")

    (entry,) = _manifest(tmp_path)["entries"]
    assert entry["status_code"] == 0
    assert entry["outcome"] == "failed"


def test_4xx_is_archived_as_failed_without_retry(tmp_path):
    url = "http://127.0.0.1:5000/alpha/dataset/missing"
    session = RouterSession({url: [FakeResponse(url, 404, body=b"not found")]})
    fetcher, sleeps, archived = _failure_fetcher(tmp_path, session)

    with pytest.raises(FetchError, match="HTTP 404"):
        fetcher.fetch(url, "alpha", "detail")
    fetcher.close()

    assert session.calls == [url]  # 4xx 不重试
    assert sleeps == []
    assert archived[0].outcome == "failed"
    (entry,) = _manifest(tmp_path)["entries"]
    assert entry["status_code"] == 404
    assert entry["outcome"] == "failed"


def test_failed_archive_does_not_pollute_successful_page(tmp_path):
    bad_url = "http://127.0.0.1:5000/alpha/dataset/broken"
    good_url = "http://127.0.0.1:5000/alpha/dataset/ok"
    good_body = b"<html><body><h1>fine</h1></body></html>"
    session = RouterSession({
        bad_url: [FakeResponse(bad_url, 503, body=b"503") for _ in range(3)],
        good_url: [FakeResponse(good_url, 200, body=good_body)],
    })
    fetcher, _, archived = _failure_fetcher(tmp_path, session, max_retries=2, backoff=0.5)

    with pytest.raises(FetchError):
        fetcher.fetch(bad_url, "alpha", "detail")
    page = fetcher.fetch(good_url, "alpha", "detail")
    fetcher.close()

    assert page.outcome == "ok"
    assert page.status_code == 200
    assert page.html == good_body.decode()
    assert Path(page.file_path).read_bytes() == good_body
    assert [item.outcome for item in archived] == ["failed", "ok"]

    entries = {entry["url"]: entry for entry in _manifest(tmp_path)["entries"]}
    assert entries[bad_url]["outcome"] == "failed"
    assert entries[good_url]["outcome"] == "ok"
    assert entries[good_url]["status_code"] == 200
