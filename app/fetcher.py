"""受限 HTTP 抓取器：回环校验、限速、大小上限、重试与原始响应留档。"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit

import requests


class FetchError(RuntimeError):
    """抓取失败（网络、状态码、响应体超限或目标越界）。"""


#: 单次重试等待的硬上限（秒）：退避再激进也不会叠加成长睡眠。
RETRY_BACKOFF_CAP = 1.0

#: 留档结果语义：ok=成功页；failed=重试耗尽后保留的失败响应（或连接错误占位记录）。
OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"


@dataclass(frozen=True)
class RawPage:
    source: str
    url: str
    kind: str
    status_code: int
    byte_count: int
    html: str
    file_path: str
    sha256: str
    content_type: str
    fetched_at: str
    outcome: str = OUTCOME_OK


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Fetcher:
    """只访问回环地址的轻量抓取器。

    raw_root/<run_id>/<sha1(url)>.html 保存原始 HTML；
    manifest.json 供 --replay-from 使用，不依赖数据库。
    """

    def __init__(
        self,
        raw_root: str | Path,
        run_id: str,
        config: dict,
        *,
        source_configs: dict | None = None,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        archive_callback: Callable[[RawPage], None] | None = None,
    ) -> None:
        self.raw_root = Path(raw_root)
        self.run_id = run_id
        self.timeout = float(config.get("timeout_seconds", 3))
        self.max_retries = min(5, max(0, int(config.get("max_retries", 1))))
        self.min_interval = max(0.0, float(config.get("min_interval_seconds", 0)))
        self.source_intervals = {
            str(name): max(0.0, float(cfg.get("min_interval_seconds", self.min_interval)))
            for name, cfg in (source_configs or {}).items()
        }
        self.max_bytes = max(1024, int(config.get("max_response_bytes", 2 * 1024 * 1024)))
        self.retry_backoff = min(
            RETRY_BACKOFF_CAP, max(0.0, float(config.get("retry_backoff_seconds", 0.05)))
        )
        self.source_backoffs = {
            str(name): min(
                RETRY_BACKOFF_CAP,
                max(0.0, float(cfg.get("retry_backoff_seconds", self.retry_backoff))),
            )
            for name, cfg in (source_configs or {}).items()
        }
        self.allowed_hosts = {
            str(h).lower() for h in config.get("allowed_hosts", ["127.0.0.1", "localhost", "::1"])
        }
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": str(config.get("user_agent", "datasheet-probe/0.1")),
                "Accept": "text/html,application/xhtml+xml",
            }
        )
        self.sleep_fn = sleep_fn
        self.archive_callback = archive_callback
        self._last_request = 0.0
        self._entries: list[dict] = []
        self._dir = self.raw_root / run_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._closed = False

    def _assert_loopback(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise FetchError(f"仅允许 http/https: {url}")
        if parts.username or parts.password:
            raise FetchError("目标 URL 不允许携带凭据")
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts or not _is_loopback(host):
            raise FetchError(f"拒绝非回环目标: {host or '<empty>'}")

    def _throttle(self, source: str) -> None:
        interval = self.source_intervals.get(source, self.min_interval)
        if interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < interval:
            self.sleep_fn(interval - elapsed)
        self._last_request = time.monotonic()

    def _request_loopback(self, url: str, source: str) -> requests.Response:
        """手动跟随重定向，并在下一次请求前校验目标仍是回环地址。"""
        response = None
        current = url
        for _ in range(4):
            self._throttle(source)
            response = self.session.get(
                current, timeout=self.timeout, stream=True, allow_redirects=False
            )
            if not response.is_redirect:
                return response
            location = response.headers.get("Location")
            final = response.url or current
            response.close()
            if not location:
                raise FetchError(f"重定向缺少 Location: {current}")
            current = urljoin(final, location)
            self._assert_loopback(current)
        if response is not None:
            response.close()
        raise FetchError(f"重定向次数超过上限: {url}")

    def _retry_wait(self, source: str, attempt: int) -> None:
        """重试前等待：经可注入的 sleep_fn 观察，单次与总量都有硬上限。"""
        base = self.source_backoffs.get(source, self.retry_backoff)
        if base <= 0:
            return
        delay = min(RETRY_BACKOFF_CAP, base * (2 ** attempt))
        if delay > 0:
            self.sleep_fn(delay)

    def fetch(self, url: str, source: str, kind: str = "page") -> RawPage:
        if self._closed:
            raise FetchError("Fetcher 已关闭")
        self._assert_loopback(url)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._request_loopback(url, source)
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._retry_wait(source, attempt)
                    continue
                page = self._archive_failure(url, source, kind, exc)
                raise FetchError(f"请求失败 {url}: {exc} (raw={page.file_path})") from exc

            final_url = response.url or url
            self._assert_loopback(final_url)
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > self.max_bytes:
                response.close()
                raise FetchError(f"响应超过上限 {self.max_bytes} 字节")
            chunks: list[bytes] = []
            size = 0
            try:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise FetchError(f"响应超过上限 {self.max_bytes} 字节")
                    chunks.append(chunk)
            finally:
                response.close()
            body = b"".join(chunks)
            if response.status_code >= 500 and attempt < self.max_retries:
                last_error = FetchError(f"上游 {response.status_code}")
                self._retry_wait(source, attempt)
                continue
            page = self._archive(response, url, final_url, body, source, kind)
            if response.status_code >= 400:
                raise FetchError(f"HTTP {response.status_code}: {url} (raw={page.file_path})")
            return page
        raise FetchError(f"请求失败 {url}: {last_error}")

    def _archive(
        self,
        response: requests.Response,
        url: str,
        final_url: str,
        body: bytes,
        source: str,
        kind: str,
    ) -> RawPage:
        encoding = response.encoding or "utf-8"
        try:
            html = body.decode(encoding, errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")
        status_code = int(response.status_code)
        outcome = OUTCOME_OK if status_code < 400 else OUTCOME_FAILED
        return self._record(
            source=source, url=url, final_url=final_url, kind=kind, body=body,
            status_code=status_code, content_type=response.headers.get("Content-Type", "text/html"),
            outcome=outcome, html=html,
        )

    def _archive_failure(self, url: str, source: str, kind: str, error: Exception) -> RawPage:
        """连接错误耗尽后的占位留档：无响应可存，写入错误说明作为失败证据。"""
        note = f"fetch failed after {self.max_retries + 1} attempts: {error}\n"
        body = note.encode("utf-8", errors="replace")
        return self._record(
            source=source, url=url, final_url=url, kind=kind, body=body,
            status_code=0, content_type="text/plain; charset=utf-8",
            outcome=OUTCOME_FAILED, html=note,
        )

    def _record(
        self,
        *,
        source: str,
        url: str,
        final_url: str,
        kind: str,
        body: bytes,
        status_code: int,
        content_type: str,
        outcome: str,
        html: str,
    ) -> RawPage:
        digest = hashlib.sha256(body).hexdigest()
        path = self._dir / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}.html"
        path.write_bytes(body)
        page = RawPage(
            source=source,
            url=url,
            kind=kind,
            status_code=status_code,
            byte_count=len(body),
            html=html,
            file_path=str(path),
            sha256=digest,
            content_type=content_type,
            fetched_at=_now(),
            outcome=outcome,
        )
        self._entries.append(
            {
                "source": source,
                "url": url,
                "final_url": final_url,
                "kind": kind,
                "file": path.name,
                "status_code": page.status_code,
                "byte_count": page.byte_count,
                "sha256": digest,
                "content_type": page.content_type,
                "fetched_at": page.fetched_at,
                "outcome": outcome,
            }
        )
        if self.archive_callback:
            self.archive_callback(page)
        return page

    def close(self) -> None:
        if self._closed:
            return
        manifest = {
            "run_id": self.run_id,
            "created_at": _now(),
            "entries": self._entries,
        }
        tmp = self._dir / "manifest.json.tmp"
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._dir / "manifest.json")
        self._closed = True
        self.session.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def raw_page_dict(self, page: RawPage) -> dict:
        return asdict(page)
