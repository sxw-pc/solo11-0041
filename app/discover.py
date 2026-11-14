"""按数据源配置翻页，发现数据集详情页 URL。

翻页由显式状态机驱动：FETCH → EXTRACT → ADVANCE →（FETCH | DONE）。
终止原因写入 DiscoveryResult.termination，调用方可以直接读取，不再只靠日志推断。

两类翻页的终止语义不同：

- ``query``：页号按 ``step`` 单调递增，永远不会指回已访问页，因此终止只由
  页号上限（``max_pages``）与内容信号（空页 / 去重后无新增）共同决定；
- ``next_link``：终止由链接目标决定——下一页 href 缺失即末页，
  href 指回已访问页即循环；``max_pages`` 只作为硬上限兜底。

两种去重各守一个状态，判定顺序不影响结果：

- ``seen_details`` 只在 EXTRACT 状态对详情 URL 去重（条目级）；
- ``visited_pages`` 只在 ADVANCE 状态拦截页面级循环（页面级）。

同一页上若多个终止条件同时成立，优先级固定为：
内容信号（空页 / 无新增）→ 链接信号（末页 / 循环）→ 页数上限。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


class TerminationReason(str, Enum):
    """翻页终止原因。"""

    LAST_PAGE = "last_page"            # 末页：没有可跟随的下一页 href（或无翻页配置）
    EMPTY_PAGE = "empty_page"          # 空页：当前页没有任何候选条目
    NO_NEW_ENTRIES = "no_new_entries"  # 当前页候选条目跨页去重后无新增
    MAX_PAGES = "max_pages"            # 达到 max_pages 硬上限
    CYCLE = "cycle"                    # 下一页 URL 指回已访问页


@dataclass
class PageReport:
    """单页发现情况。

    found: 本页解析出的候选详情链接数（含跨页重复）。
    new: 跨页去重后真正新增的条数。
    duplicates: 因 seen_details 命中而跳过的条数（found - new）。
    """

    url: str
    found: int
    new: int
    duplicates: int


@dataclass
class DiscoveryResult:
    source: str
    pages: list[object] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    page_reports: list[PageReport] = field(default_factory=list)
    termination: TerminationReason | None = None

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def discovered(self) -> int:
        return len(self.entries)

    @property
    def found(self) -> int:
        """各页解析出的候选详情链接总数（含跨页重复）。"""
        return sum(report.found for report in self.page_reports)

    @property
    def duplicates(self) -> int:
        """跨页去重跳过的详情链接总条数。"""
        return sum(report.duplicates for report in self.page_reports)


def _page_html(page: object) -> str:
    if isinstance(page, str):
        return page
    html = getattr(page, "html", None)
    if html is None:
        raise TypeError("fetch 返回值必须包含 html 属性或为字符串")
    return html


def _with_query(url: str, param: str, value: int) -> str:
    parts = urlsplit(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param]
    pairs.append((param, str(value)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), ""))


def _start_url(base_url: str, start_path: str, pagination: dict) -> str:
    url = urljoin(base_url.rstrip("/") + "/", start_path.lstrip("/"))
    if pagination.get("type", "none") == "query":
        param = str(pagination.get("param", "page"))
        start = int(pagination.get("start", 1))
        return _with_query(url, param, start)
    return url


def _item_id(url: str, pattern: str | None) -> str:
    if pattern:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else url


class _State(Enum):
    """翻页状态机的显式状态。"""

    FETCH = "fetch"        # 抓取当前列表页并登记 visited_pages
    EXTRACT = "extract"    # 解析候选详情链接，按 seen_details 做条目级去重
    ADVANCE = "advance"    # 计算下一页 URL，做页面级终止判定
    DONE = "done"          # 终止，termination 已写入结果


class _PaginationMachine:
    """单数据源的翻页状态机。

    每个 ``_State`` 对应一个转移方法，返回下一个状态；``run`` 只负责驱动到 DONE。
    """

    def __init__(
        self,
        fetch: Callable[[str, str, str], object],
        source: str,
        config: dict,
    ) -> None:
        pagination = config.get("pagination") or {}
        self.ptype = str(pagination.get("type", "none")).lower()
        self.max_pages = max(1, min(50, int(pagination.get("max_pages", 1))))
        self.page_number = int(pagination.get("start", 1))
        self.step = max(1, int(pagination.get("step", 1)))
        self.param = str(pagination.get("param", "page"))
        list_cfg = config.get("list") or {}
        self.item_selector = list_cfg.get("item_selector") or "a"
        self.link_selector = list_cfg.get("link_selector")
        self.id_pattern = list_cfg.get("item_id_regex")
        self.next_selector = pagination.get("next_selector") or list_cfg.get("next_selector")
        self.fetch = fetch
        self.source = source
        self.current = _start_url(
            str(config.get("base_url", "")), str(config.get("start_path", "/")), pagination
        )
        self.visited_pages: set[str] = set()
        self.seen_details: set[str] = set()
        self.result = DiscoveryResult(source=source)
        self._soup: BeautifulSoup | None = None

    def run(self) -> DiscoveryResult:
        state = _State.FETCH
        transitions = {
            _State.FETCH: self._fetch_page,
            _State.EXTRACT: self._extract_items,
            _State.ADVANCE: self._advance,
        }
        while state is not _State.DONE:
            state = transitions[state]()
        return self.result

    def _done(self, reason: TerminationReason) -> _State:
        self.result.termination = reason
        return _State.DONE

    def _fetch_page(self) -> _State:
        # 页面级去重：URL 在 ADVANCE 状态已被验证未访问，这里只登记，不再判定。
        self.visited_pages.add(self.current)
        raw = self.fetch(self.current, self.source, "list")
        self.result.pages.append(raw)
        self._soup = BeautifulSoup(_page_html(raw), "html.parser")
        return _State.EXTRACT

    def _extract_items(self) -> _State:
        assert self._soup is not None
        found = new = 0
        for item in self._soup.select(self.item_selector):
            link = item.select_one(self.link_selector) if self.link_selector else item
            if link is None:
                continue
            href = link.get("href") or (item.get("href") if item.name == "a" else None)
            if not href:
                continue
            detail_url = urljoin(self.current, href).split("#", 1)[0]
            if not detail_url:
                continue
            found += 1
            if detail_url in self.seen_details:
                continue
            self.seen_details.add(detail_url)
            self.result.entries.append(
                {
                    "url": detail_url,
                    "item_id": _item_id(detail_url, self.id_pattern),
                    "source": self.source,
                }
            )
            new += 1
        self.result.page_reports.append(
            PageReport(url=self.current, found=found, new=new, duplicates=found - new)
        )
        # 内容信号先于链接信号判定：空页与整页重复都在当前页上即可确认。
        if found == 0:
            return self._done(TerminationReason.EMPTY_PAGE)
        if new == 0:
            return self._done(TerminationReason.NO_NEW_ENTRIES)
        return _State.ADVANCE

    def _advance(self) -> _State:
        assert self._soup is not None
        if self.ptype == "query":
            # query：页号单调递增，next URL 必然未访问，不存在页面级循环。
            self.page_number += self.step
            nxt = _with_query(self.current, self.param, self.page_number)
        elif self.ptype == "next_link":
            node = self._soup.select_one(self.next_selector) if self.next_selector else None
            href = node.get("href") if node is not None else None
            if not href:
                return self._done(TerminationReason.LAST_PAGE)
            nxt = urljoin(self.current, href).split("#", 1)[0]
            if nxt in self.visited_pages:
                return self._done(TerminationReason.CYCLE)
        else:
            # 无翻页配置：单页即末页。
            return self._done(TerminationReason.LAST_PAGE)
        if len(self.result.pages) >= self.max_pages:
            return self._done(TerminationReason.MAX_PAGES)
        self.current = nxt
        return _State.FETCH


def discover_source(
    fetch: Callable[[str, str, str], object],
    source: str,
    config: dict,
) -> DiscoveryResult:
    """翻页并返回详情页集合。URL 去重，页数受 max_pages 硬约束。"""
    return _PaginationMachine(fetch, source, config).run()


def source_summary(result: DiscoveryResult, elapsed_ms: int) -> dict:
    """供 crawl_run 落库的摘要。"""
    return {
        "source": result.source,
        "pages": result.page_count,
        "discovered": result.discovered,
        "found": result.found,
        "duplicates": result.duplicates,
        "termination": result.termination.value if result.termination else None,
        "elapsed_ms": elapsed_ms,
        "urls": [entry["url"] for entry in result.entries],
    }
