import pytest

from app.fetcher import Fetcher, FetchError


class FakeResponse:
    def __init__(self, url: str, status_code: int, headers: dict | None = None):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308}

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
