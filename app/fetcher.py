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

from .identity import derive_item_id, item_id_from_url, stable_archive_id


class FetchError(RuntimeError):
    """抓取失败（网络、状态码、响应体超限或目标越界）。"""


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
    item_id: str = ""
    stable_id: str = ""


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

    raw_root/<run_id>/<stable_id>.html 保存原始 HTML，stable_id 由
    source、kind 与 item_id 派生；同一 run 内同一逻辑页面只保留一份文件、
    一条 manifest 条目。manifest.json 供 --replay-from 使用，不依赖数据库。
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
        self._entry_index: dict[str, int] = {}
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

    def fetch(
        self,
        url: str,
        source: str,
        kind: str = "page",
        item_id: str | None = None,
    ) -> RawPage:
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
                    continue
                raise FetchError(f"请求失败 {url}: {exc}") from exc

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
                continue
            page = self._archive(
                response, url, final_url, body, source, kind, item_id=item_id
            )
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
        *,
        item_id: str | None = None,
    ) -> RawPage:
        digest = hashlib.sha256(body).hexdigest()
        # 详情页 item_id 优先取最终 URL（跟随重定向后）的路径末段：两个入口
        # URL 收敛到同一详情页时会得到同一个 item_id 与 stable_id，从而共用
        # 一个落地文件、一条 manifest 条目和一行 raw_archive。未发生重定向
        # 时沿用发现阶段给出的 item_id，避免查询参数型详情页共用路径末段。
        if kind == "list":
            resolved_item_id = item_id or derive_item_id(url, kind)
        elif final_url != url:
            resolved_item_id = (
                item_id_from_url(final_url) or item_id or derive_item_id(url, kind)
            )
        else:
            resolved_item_id = (
                item_id or item_id_from_url(final_url) or derive_item_id(url, kind)
            )
        stable_id = stable_archive_id(source, kind, resolved_item_id)
        path = self._dir / f"{stable_id}.html"
        path.write_bytes(body)
        encoding = response.encoding or "utf-8"
        try:
            html = body.decode(encoding, errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")
        page = RawPage(
            source=source,
            url=url,
            kind=kind,
            status_code=int(response.status_code),
            byte_count=len(body),
            html=html,
            file_path=str(path),
            sha256=digest,
            content_type=response.headers.get("Content-Type", "text/html"),
            fetched_at=_now(),
            item_id=resolved_item_id,
            stable_id=stable_id,
        )
        entry = {
            "source": source,
            "url": url,
            "final_url": final_url,
            "kind": kind,
            "item_id": resolved_item_id,
            "stable_id": stable_id,
            "file": path.name,
            "status_code": page.status_code,
            "byte_count": page.byte_count,
            "sha256": digest,
            "content_type": page.content_type,
            "fetched_at": page.fetched_at,
        }
        if stable_id in self._entry_index:
            self._entries[self._entry_index[stable_id]] = entry
        else:
            self._entry_index[stable_id] = len(self._entries)
            self._entries.append(entry)
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
