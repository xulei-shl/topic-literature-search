"""CNKI 页面交互。"""

import logging
import os
import re
import time
from math import ceil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page

from browser import BrowserManager
from config import CnkiSearchConfig
from export_processor import ExportResultProcessor
from exceptions import (
    CaptchaError,
    NavigationStateError,
    TimeoutError,
    ValidationError,
)
from progress_store import SearchProgressStore
from result_parser import ResultParser
from utils import build_output_slug

logger = logging.getLogger("cnki_search.interactor")


def _load_env_file(env_path: Path) -> None:
    """加载 .env 到环境变量。"""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _find_env_path() -> Path:
    """向上查找 .env。"""
    candidate_dirs: list[Path] = []
    current_dir = Path.cwd().resolve()
    script_dir = Path(__file__).resolve().parent

    for base_dir in (current_dir, script_dir):
        candidate_dirs.extend([base_dir, *base_dir.parents])

    seen_paths: set[Path] = set()
    for directory in candidate_dirs:
        if directory in seen_paths:
            continue
        seen_paths.add(directory)

        env_path = directory / ".env"
        if env_path.exists():
            return env_path

    return current_dir / ".env"


class CnkiSearchInteractor:
    """负责执行 CNKI 各类交互。"""

    SORT_ID_MAP = {
        "relevance": "FFD",
        "date": "PT",
        "citations": "CF",
        "downloads": "DFR",
        "comprehensive": "ZH",
    }
    NEXT_PAGE_MAX_RETRIES = 3
    NEXT_PAGE_RETRY_DELAY = 1

    def __init__(self, page: Page, config: CnkiSearchConfig, browser_manager: BrowserManager):
        self.page = page
        self.config = config
        self.browser_manager = browser_manager
        self.parser = ResultParser(page)
        self.export_processor = ExportResultProcessor()

    def login(self) -> Dict[str, Any]:
        """手动登录并保存会话。"""
        self.browser_manager.wait_for_manual_login()
        self.browser_manager.save_session(page_type="home")
        return {
            "result_type": "login",
            "status": "success",
            "message": "登录状态已保存",
            "url": self.page.url,
        }

    def search(self, query: str, num_results: Optional[int] = None) -> Dict[str, Any]:
        """基础关键词检索。"""
        if not query.strip():
            raise ValidationError("检索词不能为空")

        limit = self._normalize_limit(num_results)
        self.browser_manager.restore_session(self.config.home_url)
        self._wait_for_any_selector(["textarea.search-input", "input.search-input", "#txt_SearchText"])
        self._ensure_captcha_cleared()

        self._first_visible_locator(["textarea.search-input", "input.search-input", "#txt_SearchText"]).fill(query.strip())
        self._click_first_available(["input.search-btn", "button.search-btn", ".search-btn"])

        self._wait_for_results_ready()
        results = self.parser.parse_results(limit=limit)
        results["command"] = "search"

        self.browser_manager.save_session(
            page_type="results",
            last_query=query,
            last_results_url=self.page.url,
            last_result_command="search",
        )
        return results

    def advanced_search(
        self,
        query: Optional[str],
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        core_only: bool = False,
        include_no_fulltext: bool = False,
        max_download: Optional[int] = None,
        progress_file: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """执行高级检索并分批导出完整元数据。"""
        cli_params = {
            "query": query.strip() if query else None,
            "date_from": date_from,
            "date_to": date_to,
            "core_only": core_only,
            "include_no_fulltext": include_no_fulltext,
            "max_download": max_download,
        }
        progress_store, resume_data = self._prepare_progress_store(progress_file=progress_file, cli_params=cli_params)
        resolved_params = SearchProgressStore.resolve_search_params(cli_params=cli_params, progress_data=resume_data)

        query = resolved_params["query"].strip()
        date_from = resolved_params["date_from"]
        date_to = resolved_params["date_to"]
        core_only = resolved_params["core_only"]
        include_no_fulltext = resolved_params["include_no_fulltext"]
        max_download = resolved_params["max_download"]

        self._open_advanced_search_page()
        self._wait_for_any_selector(["#gradetxt input", "input[placeholder='结束年']", "input.btn-search"])
        self._ensure_captcha_cleared()

        self._fill_advanced_search_form(
            query=query.strip(),
            date_from=date_from,
            date_to=date_to,
            core_only=core_only,
            include_no_fulltext=include_no_fulltext,
        )
        self._submit_advanced_search()
        self._wait_for_results_ready()

        summary = self.parser.parse_results_summary()
        total = summary["total"]
        planned_download = self._normalize_download_limit(max_download, total)
        output_dir = self._resolve_output_dir(query, resume_data)

        self.browser_manager.save_session(
            page_type="results",
            last_query=query,
            last_results_url=self.page.url,
            last_result_command="advanced-search",
        )

        if total <= 0:
            result = {
                "result_type": "advanced_export",
                "status": "no_results",
                "query": query,
                "total": 0,
                "selected": 0,
                "exported": 0,
                "planned_download": 0,
                "batch_count": 0,
                "exported_batches": 0,
                "core_only": core_only,
                "date_from": date_from,
                "date_to": date_to,
                "date_range": self._format_date_range(date_from, date_to),
                "url": self.page.url,
                "file_path": "",
                "final_file_path": "",
                "output_dir": str(output_dir),
                "intermediate_files": [],
                "progress_file": str(progress_store.file_path),
                "resumed_from_progress": resume_data is not None,
            }
            self._save_progress_snapshot(
                progress_store=progress_store,
                status="no_results",
                query=query,
                date_from=date_from,
                date_to=date_to,
                core_only=core_only,
                include_no_fulltext=include_no_fulltext,
                max_download=max_download,
                output_dir=output_dir,
                planned_download=0,
                batch_count=0,
                exported_total=0,
                exported_batches=0,
                next_batch_index=1,
                current_row_offset=0,
                enriched_batch_files=[],
                final_file_path="",
            )
            return result

        batch_count = ceil(planned_download / 500)
        self._set_results_per_page(min(planned_download, 500), total)

        progress_runtime = self._build_resume_runtime(
            resume_data=resume_data,
            output_dir=output_dir,
            planned_download=planned_download,
            batch_count=batch_count,
            total=total,
        )
        current_row_offset = progress_runtime["current_row_offset"]
        exported_total = progress_runtime["exported_total"]
        exported_batches = progress_runtime["exported_batches"]
        next_batch_index = progress_runtime["next_batch_index"]
        intermediate_files: list[str] = []
        enriched_batch_files = progress_runtime["enriched_batch_files"]

        self._restore_results_position(progress_runtime["current_page"])
        self._save_progress_snapshot(
            progress_store=progress_store,
            status="running",
            query=query,
            date_from=date_from,
            date_to=date_to,
            core_only=core_only,
            include_no_fulltext=include_no_fulltext,
            max_download=max_download,
            output_dir=output_dir,
            planned_download=planned_download,
            batch_count=batch_count,
            exported_total=exported_total,
            exported_batches=exported_batches,
            next_batch_index=next_batch_index,
            current_row_offset=current_row_offset,
            enriched_batch_files=enriched_batch_files,
            final_file_path="",
        )

        try:
            for batch_index in range(next_batch_index, batch_count + 1):
                batch_target = min(500, planned_download - exported_total)
                if batch_target <= 0:
                    break

                self._clear_selected_results()
                batch_selection = self._select_batch_results(batch_target, current_row_offset)
                batch_files = self._export_selected_results(query, batch_index, output_dir)
                cleaned_excel_path = output_dir / self._build_batch_file_name(query, batch_index, "metadata-cleaned", ".xlsx")
                cleaned_excel_file = self.export_processor.sanitize_export_excel(
                    excel_path=Path(batch_files["excel"]),
                    output_path=cleaned_excel_path,
                )
                try:
                    Path(batch_files["excel"]).unlink(missing_ok=True)
                except Exception as exc:
                    logger.debug("删除原始元数据文件失败: %s", exc)
                enriched_path = output_dir / self._build_batch_file_name(query, batch_index, "enriched", ".xlsx")
                enriched_file = self.export_processor.enrich_batch_excel(
                    excel_path=Path(cleaned_excel_file),
                    txt_path=Path(batch_files["txt"]),
                    output_path=enriched_path,
                )

                exported_total += batch_selection["selected_count"]
                exported_batches += 1
                current_row_offset = self._prepare_next_batch_cursor(batch_selection)
                enriched_batch_files.append(Path(enriched_file))
                intermediate_files.extend([cleaned_excel_file, batch_files["txt"], enriched_file])
                self._save_progress_snapshot(
                    progress_store=progress_store,
                    status="running",
                    query=query,
                    date_from=date_from,
                    date_to=date_to,
                    core_only=core_only,
                    include_no_fulltext=include_no_fulltext,
                    max_download=max_download,
                    output_dir=output_dir,
                    planned_download=planned_download,
                    batch_count=batch_count,
                    exported_total=exported_total,
                    exported_batches=exported_batches,
                    next_batch_index=batch_index + 1,
                    current_row_offset=current_row_offset,
                    enriched_batch_files=enriched_batch_files,
                    final_file_path="",
                )
        except KeyboardInterrupt as exc:
            self._save_progress_snapshot(
                progress_store=progress_store,
                status="interrupted",
                query=query,
                date_from=date_from,
                date_to=date_to,
                core_only=core_only,
                include_no_fulltext=include_no_fulltext,
                max_download=max_download,
                output_dir=output_dir,
                planned_download=planned_download,
                batch_count=batch_count,
                exported_total=exported_total,
                exported_batches=exported_batches,
                next_batch_index=exported_batches + 1,
                current_row_offset=current_row_offset,
                enriched_batch_files=enriched_batch_files,
                final_file_path="",
                error=exc,
            )
            raise
        except Exception as exc:
            self._save_progress_snapshot(
                progress_store=progress_store,
                status="failed",
                query=query,
                date_from=date_from,
                date_to=date_to,
                core_only=core_only,
                include_no_fulltext=include_no_fulltext,
                max_download=max_download,
                output_dir=output_dir,
                planned_download=planned_download,
                batch_count=batch_count,
                exported_total=exported_total,
                exported_batches=exported_batches,
                next_batch_index=exported_batches + 1,
                current_row_offset=current_row_offset,
                enriched_batch_files=enriched_batch_files,
                final_file_path="",
                error=exc,
            )
            raise

        final_file_path = ""
        if enriched_batch_files:
            final_file = output_dir / self._build_summary_file_name(query)
            final_file_path = self.export_processor.merge_batch_excels(enriched_batch_files, final_file)

        self._save_progress_snapshot(
            progress_store=progress_store,
            status="success",
            query=query,
            date_from=date_from,
            date_to=date_to,
            core_only=core_only,
            include_no_fulltext=include_no_fulltext,
            max_download=max_download,
            output_dir=output_dir,
            planned_download=planned_download,
            batch_count=batch_count,
            exported_total=exported_total,
            exported_batches=exported_batches,
            next_batch_index=batch_count + 1,
            current_row_offset=current_row_offset,
            enriched_batch_files=enriched_batch_files,
            final_file_path=final_file_path,
        )

        return {
            "result_type": "advanced_export",
            "status": "success",
            "query": query,
            "total": total,
            "selected": exported_total,
            "exported": exported_total,
            "planned_download": planned_download,
            "batch_count": batch_count,
            "exported_batches": exported_batches,
            "core_only": core_only,
            "date_from": date_from,
            "date_to": date_to,
            "date_range": self._format_date_range(date_from, date_to),
            "url": self.page.url,
            "file_path": final_file_path,
            "final_file_path": final_file_path,
            "output_dir": str(output_dir),
            "intermediate_files": intermediate_files,
            "progress_file": str(progress_store.file_path),
            "resumed_from_progress": resume_data is not None,
        }

    def _prepare_progress_store(
        self,
        progress_file: Optional[Path],
        cli_params: Dict[str, Any],
    ) -> tuple[SearchProgressStore, Optional[Dict[str, Any]]]:
        """初始化进度文件存储并按需读取历史进度。"""
        if progress_file is not None:
            progress_store = SearchProgressStore(progress_file)
            resume_data = progress_store.load() if progress_store.exists() else None
            return progress_store, resume_data

        query = cli_params.get("query")
        if not query:
            raise ValidationError("高级检索至少需要提供 --query 或 --progress-file")

        output_dir = self.config.ensure_output_dir(query)
        default_path = SearchProgressStore.build_default_path(
            output_dir=output_dir,
            query=query,
            date_from=cli_params.get("date_from"),
            date_to=cli_params.get("date_to"),
            core_only=bool(cli_params.get("core_only")),
            include_no_fulltext=bool(cli_params.get("include_no_fulltext")),
            max_download=cli_params.get("max_download"),
        )
        return SearchProgressStore(default_path), None

    def _resolve_output_dir(self, query: str, resume_data: Optional[Dict[str, Any]]) -> Path:
        """返回当前任务应使用的输出目录。"""
        runtime = (resume_data or {}).get("runtime") or {}
        output_dir = runtime.get("output_dir")
        if output_dir:
            return Path(output_dir).resolve()
        return self.config.ensure_output_dir(query)

    def _build_resume_runtime(
        self,
        resume_data: Optional[Dict[str, Any]],
        output_dir: Path,
        planned_download: int,
        batch_count: int,
        total: int,
    ) -> Dict[str, Any]:
        """根据历史进度构造恢复运行态。"""
        if not resume_data:
            return {
                "exported_total": 0,
                "exported_batches": 0,
                "next_batch_index": 1,
                "current_page": 1,
                "current_row_offset": 0,
                "enriched_batch_files": [],
            }

        status = resume_data.get("status")
        if status == "success":
            raise ValidationError("该进度文件已执行完成，无需继续")
        if status == "no_results":
            raise ValidationError("该进度文件对应的检索结果为空，无需继续")

        runtime = resume_data.get("runtime") or {}
        exported_total = int(runtime.get("exported_total") or 0)
        exported_batches = int(runtime.get("exported_batches") or 0)
        next_batch_index = int(runtime.get("next_batch_index") or (exported_batches + 1))
        current_page = int(runtime.get("current_page") or 1)
        current_row_offset = int(runtime.get("current_row_offset") or 0)
        enriched_batch_files = [Path(file_path).resolve() for file_path in runtime.get("enriched_batch_files") or []]

        self._validate_resume_batch_files(enriched_batch_files)

        if exported_total > planned_download:
            raise ValidationError("进度文件中的已导出数量超过本次计划导出数量")
        if exported_total > total:
            raise ValidationError("当前检索结果总数小于进度文件中的已导出数量，无法安全恢复")
        if exported_batches > batch_count:
            raise ValidationError("进度文件中的已导出批次数超过本次批次数")

        return {
            "exported_total": exported_total,
            "exported_batches": exported_batches,
            "next_batch_index": next_batch_index,
            "current_page": current_page,
            "current_row_offset": current_row_offset,
            "enriched_batch_files": enriched_batch_files,
            "output_dir": output_dir.resolve(),
        }

    def _validate_resume_batch_files(self, enriched_batch_files: list[Path]) -> None:
        """校验历史批次文件是否仍然存在。"""
        for file_path in enriched_batch_files:
            if not file_path.exists():
                raise ValidationError(f"进度文件引用的批次文件不存在: {file_path}")

    def _restore_results_position(self, target_page: int) -> None:
        """恢复到需要续跑的结果页，优先使用可见数字页跳转。"""
        if target_page <= 1:
            return

        current_page = self.parser.parse_results_summary()["current_page"]
        if current_page > target_page:
            raise ValidationError(f"当前结果页码大于目标恢复页码: {current_page} > {target_page}")

        while current_page < target_page:
            page_link = self._find_resume_target_page_link(current_page=current_page, target_page=target_page)
            if page_link is not None:
                moved = self._goto_results_page_by_link(page_link)
            else:
                moved = self._goto_next_results_page()
            if not moved:
                raise ValidationError(f"未能恢复到目标页码: {target_page}")
            current_page = self.parser.parse_results_summary()["current_page"]

    def _save_progress_snapshot(
        self,
        progress_store: SearchProgressStore,
        status: str,
        query: str,
        date_from: Optional[str],
        date_to: Optional[str],
        core_only: bool,
        include_no_fulltext: bool,
        max_download: Optional[int],
        output_dir: Path,
        planned_download: int,
        batch_count: int,
        exported_total: int,
        exported_batches: int,
        next_batch_index: int,
        current_row_offset: int,
        enriched_batch_files: list[Path],
        final_file_path: str,
        error: Optional[BaseException] = None,
    ) -> None:
        """写入当前高级检索进度快照。"""
        page_context = self._safe_progress_page_context()
        state: Dict[str, Any] = {
            "version": SearchProgressStore.VERSION,
            "status": status,
            "search_params": {
                "query": query,
                "date_from": date_from,
                "date_to": date_to,
                "core_only": core_only,
                "include_no_fulltext": include_no_fulltext,
                "max_download": max_download,
            },
            "runtime": {
                "output_dir": str(output_dir.resolve()),
                "planned_download": planned_download,
                "batch_count": batch_count,
                "exported_total": exported_total,
                "exported_batches": exported_batches,
                "next_batch_index": next_batch_index,
                "current_page": page_context["current_page"],
                "page_text": page_context["page_text"],
                "current_row_offset": current_row_offset,
                "enriched_batch_files": [str(file_path.resolve()) for file_path in enriched_batch_files],
                "final_file_path": final_file_path,
                "last_known_url": page_context["url"],
            },
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        if error is not None:
            state["last_error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }

        progress_store.save(state)
        logger.info(
            "高级检索进度已写入: status=%s, file=%s, batches=%s/%s",
            status,
            progress_store.file_path,
            exported_batches,
            batch_count,
        )

    def _safe_progress_page_context(self) -> Dict[str, Any]:
        """尽量提取结果页上下文，用于进度留痕。"""
        try:
            summary = self.parser.parse_results_summary()
            return {
                "current_page": int(summary.get("current_page") or 1),
                "page_text": summary.get("page", ""),
                "url": self.page.url,
            }
        except Exception as exc:
            logger.debug("提取进度页上下文失败: %s", exc)
            return {
                "current_page": 1,
                "page_text": "",
                "url": self.page.url if self.page else "",
            }

    def parse_current_results(self, url: Optional[str] = None, num_results: Optional[int] = None) -> Dict[str, Any]:
        """重新解析结果页。"""
        limit = self._normalize_limit(num_results)
        target_url = url or self._resolve_results_url()
        self.browser_manager.restore_session(target_url)
        self._wait_for_results_ready()

        results = self.parser.parse_results(limit=limit)
        results["command"] = "parse_page"
        self.browser_manager.save_session(
            page_type="results",
            last_results_url=self.page.url,
        )
        return results

    def navigate_results(
        self,
        action: str,
        value: Optional[str] = None,
        num_results: Optional[int] = None,
    ) -> Dict[str, Any]:
        """翻页或排序。"""
        limit = self._normalize_limit(num_results)
        self.browser_manager.restore_session(self._resolve_results_url())
        self._wait_for_results_ready()

        previous_url = self.page.url
        previous_page = self.parser.parse_results_summary()["page"]
        previous_title = self._first_result_title()
        previous_sort = self._current_sort_text()

        if action == "sort":
            sort_key = (value or "").strip().lower()
            sort_id = self.SORT_ID_MAP.get(sort_key)
            if not sort_id:
                raise ValidationError("排序方式仅支持 relevance/date/citations/downloads/comprehensive")
            self.page.locator(f"#orderList li#{sort_id}").first.click()
        elif action in {"next", "previous"}:
            target_text = "下一页" if action == "next" else "上一页"
            target_link = self.page.locator(".pages a").filter(has_text=target_text).first
            if target_link.count() == 0:
                raise NavigationStateError(f"当前页面无法执行 {action}")
            target_link.click()
        elif action == "page":
            if not value or not value.isdigit():
                raise ValidationError("page 操作需要提供数字页码")
            target_link = self.page.locator(".pages a").filter(has_text=value).first
            if target_link.count() == 0:
                raise NavigationStateError(f"当前结果页中未找到页码 {value}")
            target_link.click()
        else:
            raise ValidationError("navigate 仅支持 next/previous/page/sort")

        self._wait_for_results_changed(previous_url, previous_page, previous_title, previous_sort)
        results = self.parser.parse_results(limit=limit)
        results["command"] = "navigate"
        results["navigate"] = {"action": action, "value": value or ""}

        self.browser_manager.save_session(
            page_type="results",
            last_results_url=self.page.url,
        )
        return results

    def get_paper_detail(self, url: Optional[str] = None, index: Optional[int] = None) -> Dict[str, Any]:
        """提取论文详情。"""
        target_url = url or self._resolve_detail_url(index)
        self.browser_manager.restore_session(target_url)
        self._wait_for_any_selector([".brief h1"])
        self._ensure_captcha_cleared()

        detail = self.parser.parse_paper_detail()
        self.browser_manager.save_session(
            page_type="detail",
            last_detail_url=self.page.url,
        )
        return detail

    def _fill_advanced_search_form(
        self,
        query: str,
        date_from: Optional[str],
        date_to: Optional[str],
        core_only: bool,
        include_no_fulltext: bool,
    ) -> None:
        self._disable_checkbox("input[data-id='EN'][name='onlyChecked']")
        self._ensure_advanced_condition_rows(2)
        self._set_advanced_condition(0, "主题", query)
        self._set_advanced_condition(1, "篇关摘", query, logic="OR")
        if date_from:
            self._set_input_value(["input[placeholder='起始年']", "input[placeholder*='起始']"], date_from)

        if date_to:
            self._set_input_value(["input[placeholder='结束年']", "input[placeholder*='结束']"], date_to)

        if include_no_fulltext:
            self._disable_checkbox("#onlyfulltext")

        if core_only:
            self._disable_checkbox("input[name='all']")
            for selector in [
                "input[key='LYBSM'][value='P12']",
                "input[key='SI'][value='Y']",
                "input[key='EI'][value='Y']",
                "input[key='HX'][value='Y']",
                "input[key='CSI'][value='Y']",
                "input[key='CSD'][value='Y']",
                "input[key='AMI'][value='P13']",
            ]:
                self._enable_checkbox(selector)

    def _open_advanced_search_page(self) -> None:
        self.browser_manager.restore_session(self.config.home_url)
        self._ensure_captcha_cleared()

        link = self.page.locator("#highSearch").first
        if link.count() == 0:
            raise ValidationError("首页未找到“高级检索”入口")

        link.click()
        time.sleep(0.5)
        self.page.goto(self.config.advanced_search_url, timeout=self.config.navigation_timeout * 1000)
        self.page.wait_for_load_state("domcontentloaded")

        if not self._is_advanced_search_page(self.page):
            raise ValidationError("打开统一高级检索页面失败")

        self.browser_manager._page = self.page
        self.parser = ResultParser(self.page)

    def _is_advanced_search_page(self, target_page: Page) -> bool:
        selectors = ["#gradetxt", "input[placeholder='结束年']", "#onlyfulltext", "input.btn-search"]
        for selector in selectors:
            locator_group = target_page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=200)
                    return True
                except Exception:
                    continue
        return False

    def _submit_advanced_search(self) -> None:
        if not self._click_first_available(["input.btn-search", "div.search", ".btn-search"]):
            raise ValidationError("未找到高级检索提交按钮")
        self._dismiss_dialog_if_present()

    def _set_results_per_page(self, export_limit: int, total_results: int) -> None:
        if export_limit <= 20 or total_results <= 20:
            return

        try:
            dropdown = self.page.locator("#perPageDiv .sort-default").first
            if dropdown.count() == 0:
                return

            previous_rows = self.page.locator(".result-table-list tbody tr").count()
            dropdown.click()
            option = self.page.locator("#perPageDiv .sort-list li[data-val='50'] a").first
            if option.count() == 0:
                option = self.page.locator("#perPageDiv .sort-list a").filter(has_text="50").first
            if option.count() == 0:
                return

            previous_page = self.parser.parse_results_summary()["page"]
            option.click()
            self._wait_for_results_page_changed(previous_page, previous_rows)
        except Exception as exc:
            logger.debug("设置每页 50 条失败: %s", exc)

    def _clear_selected_results(self) -> None:
        clear_link = self.page.locator(".checkcount a").filter(has_text="清除").first
        if clear_link.count() == 0:
            return
        try:
            clear_link.click()
            time.sleep(0.3)
        except Exception as exc:
            logger.debug("清除已选文献失败: %s", exc)

    def _select_batch_results(self, export_limit: int, row_offset: int) -> Dict[str, Any]:
        remaining = export_limit
        selected_count = 0
        current_row_offset = row_offset
        current_row_count = 0

        while remaining > 0:
            self._wait_for_results_ready()
            row_locator = self.page.locator(".result-table-list tbody tr")
            current_row_count = row_locator.count()
            if current_row_count == 0:
                break

            if current_row_offset >= current_row_count:
                if not self._goto_next_results_page():
                    break
                current_row_offset = 0
                continue

            page_target_count = min(current_row_count - current_row_offset, remaining)
            self._select_rows_on_current_page(current_row_offset, page_target_count, current_row_count)
            selected_count = self._extract_selected_count(selected_count + page_target_count)
            remaining = export_limit - selected_count
            current_row_offset += page_target_count
            if remaining <= 0:
                break

            if current_row_offset < current_row_count:
                continue

            if not self._goto_next_results_page():
                break
            current_row_offset = 0

        if selected_count <= 0:
            raise ValidationError("结果页未选中任何文献，无法导出")
        return {
            "selected_count": selected_count,
            "next_row_offset": current_row_offset,
            "page_row_count": current_row_count,
        }

    def _select_rows_on_current_page(self, row_offset: int, page_target_count: int, row_count: int) -> None:
        checkbox_locator = self.page.locator(".result-table-list tbody input.cbItem")
        if row_offset == 0 and page_target_count == row_count and self.page.locator("#selectCheckAll1").count() > 0:
            self._ensure_checkbox_checked(
                self.page.locator("#selectCheckAll1").first,
                selector="#selectCheckAll1",
            )
            return

        for index in range(row_offset, row_offset + page_target_count):
            checkbox = checkbox_locator.nth(index)
            self._ensure_checkbox_checked(checkbox, selector=f".result-table-list tbody input.cbItem[{index}]")

    def _ensure_checkbox_checked(self, checkbox: Locator, selector: str = "") -> None:
        """稳定勾选复选框，必要时退回 JS 兜底。"""
        try:
            if checkbox.is_checked():
                return
        except Exception:
            pass

        try:
            checkbox.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("复选框滚动到可视区域失败: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            checkbox.check(force=True, timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("常规勾选复选框失败，尝试使用 JS 兜底: selector=%s, error=%s", selector or "<locator>", exc)
            checkbox.evaluate(
                """
                (element) => {
                    element.checked = true;
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    element.dispatchEvent(new Event('click', { bubbles: true }));
                }
                """
            )

        try:
            if checkbox.is_checked():
                return
        except Exception as exc:
            logger.debug("校验复选框勾选状态失败: selector=%s, error=%s", selector or "<locator>", exc)

        raise TimeoutError(f"未能完成复选框勾选: {selector or '<locator>'}")

    def _prepare_next_batch_cursor(self, batch_selection: Dict[str, Any]) -> int:
        next_row_offset = batch_selection["next_row_offset"]
        page_row_count = batch_selection["page_row_count"]
        if next_row_offset < page_row_count:
            return next_row_offset

        if self._goto_next_results_page():
            return 0
        return next_row_offset

    def _action_timeout_ms(self) -> int:
        """返回单次页面动作超时。"""
        timeout_seconds = getattr(self.config, "action_timeout", self.config.page_timeout)
        return max(int(timeout_seconds * 1000), 1000)

    def _page_change_timeout_seconds(self) -> float:
        """返回结果页变化等待超时。"""
        timeout_seconds = getattr(self.config, "page_change_timeout", self.config.page_timeout)
        return max(float(timeout_seconds), 1.0)

    def _has_results_state_changed(
        self,
        previous_url: str,
        previous_page: str,
        previous_title: str,
        previous_sort: str,
    ) -> bool:
        """判断结果页状态是否已经发生变化。"""
        current_url = self.page.url
        current_page = self.parser.parse_results_summary()["page"]
        current_title = self._first_result_title()
        current_sort = self._current_sort_text()
        return any(
            [
                current_url != previous_url,
                current_page != previous_page,
                current_title != previous_title,
                current_sort != previous_sort,
            ]
        )

    def _click_next_page_link(self, next_link: Locator, attempt: int) -> None:
        """执行结果页“下一页”点击。"""
        try:
            next_link.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("翻页按钮滚动到可视区域失败: %s", exc)

        if attempt < self.NEXT_PAGE_MAX_RETRIES:
            next_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            return

        try:
            next_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        except Exception as click_exc:
            logger.debug("常规点击“下一页”失败，尝试使用 JS 点击: %s", click_exc)
            next_link.evaluate("(element) => element.click()")

    def _click_page_number_link(self, page_link: Locator, attempt: int) -> None:
        """执行结果页数字页码点击。"""
        try:
            page_link.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("页码按钮滚动到可视区域失败: %s", exc)

        if attempt < self.NEXT_PAGE_MAX_RETRIES:
            page_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            return

        try:
            page_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        except Exception as click_exc:
            logger.debug("常规点击页码失败，尝试使用 JS 点击: %s", click_exc)
            page_link.evaluate("(element) => element.click()")

    def _goto_next_results_page(self) -> bool:
        previous_url = self.page.url
        previous_page = self.parser.parse_results_summary()["page"]
        previous_title = self._first_result_title()
        previous_sort = self._current_sort_text()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            next_link = self._find_next_page_link()
            if next_link is None:
                return False

            class_name = next_link.get_attribute("class") or ""
            if "disabled" in class_name:
                return False

            try:
                self._click_next_page_link(next_link, attempt)
                self._wait_for_results_changed(
                    previous_url,
                    previous_page,
                    previous_title,
                    previous_sort,
                    timeout=self._page_change_timeout_seconds(),
                )
                return True
            except Exception as exc:
                last_error = exc
                try:
                    if self._has_results_state_changed(previous_url, previous_page, previous_title, previous_sort):
                        return True
                except Exception:
                    pass

                logger.warning(
                    "结果页翻页失败，准备重试: attempt=%s/%s, error=%s",
                    attempt,
                    self.NEXT_PAGE_MAX_RETRIES,
                    exc,
                )
                self._dismiss_dialog_if_present()
                self._ensure_captcha_cleared()
                if attempt < self.NEXT_PAGE_MAX_RETRIES:
                    time.sleep(self.NEXT_PAGE_RETRY_DELAY)

        raise TimeoutError(f"结果页翻页失败，已重试 {self.NEXT_PAGE_MAX_RETRIES} 次: {last_error}")

    def _goto_results_page_by_link(self, page_link: Locator) -> bool:
        """点击数字页码并等待结果页完成跳转。"""
        previous_url = self.page.url
        previous_page = self.parser.parse_results_summary()["page"]
        previous_title = self._first_result_title()
        previous_sort = self._current_sort_text()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            try:
                self._click_page_number_link(page_link, attempt)
                self._wait_for_results_changed(
                    previous_url,
                    previous_page,
                    previous_title,
                    previous_sort,
                    timeout=self._page_change_timeout_seconds(),
                )
                return True
            except Exception as exc:
                last_error = exc
                try:
                    if self._has_results_state_changed(previous_url, previous_page, previous_title, previous_sort):
                        return True
                except Exception:
                    pass

                logger.warning(
                    "结果页页码跳转失败，准备重试: attempt=%s/%s, error=%s",
                    attempt,
                    self.NEXT_PAGE_MAX_RETRIES,
                    exc,
                )
                self._dismiss_dialog_if_present()
                self._ensure_captcha_cleared()
                if attempt < self.NEXT_PAGE_MAX_RETRIES:
                    time.sleep(self.NEXT_PAGE_RETRY_DELAY)

        raise TimeoutError(f"结果页页码跳转失败，已重试 {self.NEXT_PAGE_MAX_RETRIES} 次: {last_error}")

    def _export_selected_results(self, query: str, batch_index: int, output_dir: Path) -> Dict[str, str]:
        self._open_export_menu()
        export_page = self._open_custom_export_page()
        if export_page is None and self._is_personal_login_visible():
            self._login_personal_account()
            self._open_export_menu()
            export_page = self._open_custom_export_page()

        if export_page is None:
            raise ValidationError("未能打开自定义导出页面")

        try:
            export_page.wait_for_load_state("domcontentloaded", timeout=20000)
            export_page.wait_for_selector(".check-labels", timeout=20000)

            self._click_link_by_text("全选", page=export_page)
            excel_path = self._download_from_export_page(
                export_page=export_page,
                selectors=["#litoexcel"],
                output_dir=output_dir,
                query=query,
                batch_index=batch_index,
                kind="metadata",
                default_name="cnki-export.xls",
            )
            if not self._click_first_available(
                ["a[displaymode='GBTREFER']", "li.current a[displaymode='GBTREFER']"],
                page=export_page,
            ):
                self._click_link_by_text("GB/T 7714-2015 格式引文", page=export_page)
            time.sleep(0.3)
            txt_path = self._download_from_export_page(
                export_page=export_page,
                selectors=["#litotxt"],
                output_dir=output_dir,
                query=query,
                batch_index=batch_index,
                kind="reference",
                default_name="cnki-reference.txt",
            )
            return {"excel": excel_path, "txt": txt_path}
        finally:
            try:
                export_page.close()
            except Exception:
                pass

    def _download_from_export_page(
        self,
        export_page: Page,
        selectors: list[str],
        output_dir: Path,
        query: str,
        batch_index: int,
        kind: str,
        default_name: str,
    ) -> str:
        with export_page.expect_download(timeout=45000) as download_info:
            if not self._click_first_available(selectors, page=export_page):
                raise ValidationError(f"未找到导出按钮: {kind}")

        download = download_info.value
        file_path = self._build_export_file_path(
            output_dir=output_dir,
            query=query,
            batch_index=batch_index,
            kind=kind,
            suggested_name=download.suggested_filename or default_name,
        )
        download.save_as(str(file_path))
        return str(file_path)

    def _build_export_file_path(
        self,
        output_dir: Path,
        query: str,
        batch_index: int,
        kind: str,
        suggested_name: str,
    ) -> Path:
        suffix = Path(suggested_name).suffix or ".xls"
        filename = self._build_batch_file_name(query, batch_index, kind, suffix)
        return output_dir / filename

    def _build_batch_file_name(self, query: str, batch_index: int, kind: str, suffix: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = build_output_slug(query)[:40]
        return f"{timestamp}-{slug}-batch{batch_index:03d}-{kind}{suffix}"

    def _build_summary_file_name(self, query: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = build_output_slug(query)[:40]
        return f"{timestamp}-{slug}-merged.xlsx"

    def _open_export_menu(self) -> None:
        self._click_link_by_text("导出与分析")
        self._click_link_by_text("导出文献")

    def _open_custom_export_page(self, timeout: int = 15) -> Optional[Page]:
        existing_pages = list(self.page.context.pages)
        if not self._click_first_available(["a[exporttype='selfDefine']"]):
            raise ValidationError("未找到“自定义”导出入口")

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_personal_login_visible():
                return None

            for opened_page in self.page.context.pages:
                if opened_page not in existing_pages:
                    return opened_page

            time.sleep(0.3)

        if self._is_personal_login_visible():
            return None
        return None

    def _is_personal_login_visible(self) -> bool:
        for selector in [
            ".ecp-account-login .ecp_userName",
            ".ecp-passwordBox .ecp_passWord",
            "button.ECP_UserLOgin",
        ]:
            locator = self.page.locator(selector)
            if locator.count() == 0:
                continue
            for index in range(locator.count()):
                try:
                    if locator.nth(index).is_visible():
                        return True
                except Exception:
                    continue
        return False

    def _login_personal_account(self) -> None:
        username, password, env_path = self._load_personal_login_credentials()

        username_input = self._first_visible_locator(["input.ecp_userName"])
        password_input = self._first_visible_locator(["input.ecp_passWord"])
        agreement_checkbox = self._first_visible_locator(["#agreement"])

        username_input.fill(username)
        password_input.fill(password)

        if not agreement_checkbox.is_checked():
            agreement_checkbox.check(force=True)

        if not self._click_first_available(["button.ECP_UserLOgin"]):
            raise ValidationError("未找到个人登录按钮")

        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            if not self._is_personal_login_visible():
                return
            if self.browser_manager.is_captcha_visible(self.page):
                self._ensure_captcha_cleared()
            time.sleep(0.5)

        raise ValidationError(f"个人登录未完成，请检查 .env 中的账号密码是否正确：{env_path}")

    def _load_personal_login_credentials(self) -> tuple[str, str, Path]:
        env_path = _find_env_path()
        _load_env_file(env_path)

        username = ""
        password = ""
        for key in ["CNKI_USERNAME", "CNKI_USER", "CNKI_ACCOUNT"]:
            username = os.environ.get(key, "").strip()
            if username:
                break

        for key in ["CNKI_PASSWORD", "CNKI_PASS"]:
            password = os.environ.get(key, "").strip()
            if password:
                break

        if username and password:
            return username, password, env_path

        raise ValidationError(
            f"检测到个人登录弹框，但未在 {env_path} 中找到 CNKI_USERNAME / CNKI_PASSWORD 配置"
        )

    def _disable_checkbox(self, selector: str) -> None:
        checkbox = self.page.locator(selector).first
        if checkbox.count() == 0:
            return
        try:
            if checkbox.is_checked():
                checkbox.evaluate(
                    """
                    (element) => {
                        element.checked = false;
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                        element.dispatchEvent(new Event('click', { bubbles: true }));
                    }
                    """
                )
                checkbox.uncheck(force=True)
                if checkbox.is_checked():
                    raise ValidationError(f"未能取消勾选控件: {selector}")
        except Exception as exc:
            logger.debug("取消勾选失败: %s", exc)

    def _enable_checkbox(self, selector: str) -> None:
        checkbox = self.page.locator(selector).first
        if checkbox.count() == 0:
            return
        try:
            if not checkbox.is_checked():
                checkbox.check(force=True)
        except Exception as exc:
            logger.debug("勾选复选框失败: %s", exc)

    def _ensure_advanced_condition_rows(self, required_count: int) -> None:
        rows = self.page.locator("#gradetxt dd")
        deadline = time.time() + self.config.page_timeout
        while rows.count() < required_count and time.time() < deadline:
            if not self._click_first_available(
                ["#gradetxt a.add-group", "#gradetxt .add-group", "a.add-group"],
                page=self.page,
            ):
                break
            time.sleep(0.3)
        if rows.count() < required_count:
            raise ValidationError("高级检索条件行不足，无法配置双条件检索")

    def _set_advanced_condition(self, row_index: int, field_title: str, query: str, logic: str = "") -> None:
        row = self.page.locator("#gradetxt dd").nth(row_index)
        if row.count() == 0:
            raise ValidationError(f"未找到第 {row_index + 1} 条高级检索条件")

        if logic:
            self._select_dropdown_option(
                row.locator(".sort.logical").first,
                row.locator(f".sort.logical .sort-list a[value='{logic}']").first,
            )

        trigger = row.locator(".sort.reopt").first
        option = row.locator(f".sort.reopt .sort-list a[title='{field_title}']").first
        self._select_dropdown_option(trigger, option, force=True)

        query_input = row.locator(".input-box > input[type='text']").first
        if query_input.count() == 0:
            raise ValidationError(f"未找到第 {row_index + 1} 条检索词输入框")
        query_input.fill(query)

    def _select_dropdown_option(self, trigger: Locator, option: Locator, force: bool = False) -> None:
        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            try:
                trigger.wait_for(state="visible", timeout=500)
                trigger.click()
                option.wait_for(state="visible", timeout=500)
                option.click(force=force)
                return
            except Exception as exc:
                logger.debug("等待高级检索下拉项失败: %s", exc)
                time.sleep(0.2)

        raise ValidationError("高级检索下拉项不存在")

    def _set_input_value(self, selectors: list[str], value: str) -> None:
        locator = self._first_visible_locator(selectors)
        locator.evaluate(
            """
            (element, inputValue) => {
                element.removeAttribute('readonly');
                element.value = inputValue;
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            value,
        )

    def _find_next_page_link(self) -> Optional[Locator]:
        selectors = ["#PageNext", "#Page_next_top", "a#Page_next_top", ".pages a"]
        for selector in selectors:
            locator_group = self.page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    text = locator.inner_text().strip()
                except Exception:
                    text = ""
                if selector == ".pages a" and "下一页" not in text:
                    continue
                return locator
        return None

    def _find_resume_target_page_link(self, current_page: int, target_page: int) -> Optional[Locator]:
        """查找恢复续跑时可直接点击的目标数字页。"""
        page_links = self.page.locator(".pages a[data-curpage]")
        candidate_link: Optional[Locator] = None
        candidate_page = current_page

        for index in range(page_links.count()):
            link = page_links.nth(index)
            try:
                text = link.inner_text().strip()
            except Exception:
                continue
            if not text.isdigit():
                continue

            raw_page = link.get_attribute("data-curpage") or text
            if not raw_page.isdigit():
                continue

            page_number = int(raw_page)
            if page_number <= current_page or page_number > target_page:
                continue

            class_name = (link.get_attribute("class") or "").strip()
            if "cur" in class_name:
                continue

            if page_number == target_page:
                return link

            if page_number > candidate_page:
                candidate_page = page_number
                candidate_link = link

        return candidate_link

    def _click_link_by_text(self, text: str, page: Optional[Page] = None) -> None:
        target_page = page or self.page
        links = target_page.locator("a").filter(has_text=text)
        if links.count() == 0:
            raise ValidationError(f"未找到链接: {text}")
        for index in range(links.count()):
            link = links.nth(index)
            try:
                link.wait_for(state="visible", timeout=800)
                link.click()
                return
            except Exception:
                continue
        raise ValidationError(f"未找到可见链接: {text}")

    def _extract_selected_count(self, default_value: int) -> int:
        locator = self.page.locator("#selectCount").first
        if locator.count() == 0:
            return default_value
        text = locator.inner_text().replace(",", "").replace("，", "").strip()
        return int(text) if text.isdigit() else default_value

    def _resolve_detail_url(self, index: Optional[int]) -> str:
        if index is not None:
            if index <= 0:
                raise ValidationError("论文序号必须大于 0")
            page_data = self.parse_current_results(num_results=self.config.max_results)
            for item in page_data["results"]:
                if item["n"] == index:
                    return item["href"]
            raise NavigationStateError(f"当前结果页不存在第 {index} 条论文")

        state = self.browser_manager.read_state()
        last_detail_url = state.get("last_detail_url")
        if last_detail_url:
            return last_detail_url
        raise NavigationStateError("未提供论文 URL，且当前没有可复用的详情页记录")

    def _resolve_results_url(self) -> str:
        state = self.browser_manager.read_state()
        last_results_url = state.get("last_results_url")
        if last_results_url:
            return last_results_url
        raise NavigationStateError("当前没有可复用的搜索结果页，请先执行 search 或 advanced-search")

    def _wait_for_selector(self, selector: str, timeout: Optional[int] = None) -> None:
        self.page.locator(selector).first.wait_for(
            state="visible",
            timeout=(timeout or self.config.page_timeout) * 1000,
        )

    def _wait_for_any_selector(self, selectors: list[str], timeout: Optional[int] = None) -> None:
        deadline = time.time() + (timeout or self.config.page_timeout)
        while time.time() < deadline:
            for selector in selectors:
                locator_group = self.page.locator(selector)
                if locator_group.count() == 0:
                    continue
                for index in range(locator_group.count()):
                    locator = locator_group.nth(index)
                    try:
                        locator.wait_for(state="visible", timeout=300)
                        return
                    except Exception:
                        continue
            time.sleep(0.2)
        raise TimeoutError(f"等待页面元素超时: {selectors[0]}")

    def _wait_for_results_ready(self) -> None:
        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            if self.browser_manager.is_captcha_visible(self.page):
                self._ensure_captcha_cleared()

            if self.page.locator(".result-table-list tbody tr").count() > 0:
                return

            if self.page.locator("#ModuleSearchResult .no-content").count() > 0:
                return

            if self.page.locator(".pagerTitleCell").count() > 0:
                title_text = self.page.locator(".pagerTitleCell").first.inner_text()
                if "条结果" in title_text:
                    return

            time.sleep(0.5)

        raise TimeoutError("等待结果页超时")

    def _wait_for_results_changed(
        self,
        previous_url: str,
        previous_page: str,
        previous_title: str,
        previous_sort: str,
        timeout: Optional[float] = None,
    ) -> None:
        deadline = time.time() + (timeout or self._page_change_timeout_seconds())
        while time.time() < deadline:
            if self.browser_manager.is_captcha_visible(self.page):
                self._ensure_captcha_cleared()

            try:
                if self._has_results_state_changed(previous_url, previous_page, previous_title, previous_sort):
                    return
            except Exception as exc:
                logger.debug("结果页状态刷新中，继续等待: %s", exc)
            time.sleep(0.5)

        raise TimeoutError("等待翻页或排序完成超时")

    def _wait_for_results_page_changed(self, previous_page: str, previous_rows: int) -> None:
        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            try:
                current_page = self.parser.parse_results_summary()["page"]
                current_rows = self.page.locator(".result-table-list tbody tr").count()
                if current_page != previous_page or current_rows != previous_rows:
                    return
            except Exception as exc:
                logger.debug("等待结果页分页状态变化时遇到瞬时异常: %s", exc)
            time.sleep(0.5)

    def _dismiss_dialog_if_present(self) -> None:
        dialog = self.page.locator(".layui-layer-dialog").first
        if dialog.count() == 0:
            return
        confirm_button = self.page.locator(".layui-layer-btn0").first
        if confirm_button.count() == 0:
            return
        confirm_button.click()

    def _ensure_captcha_cleared(self) -> None:
        if not self.browser_manager.is_captcha_visible(self.page):
            return
        if not self.browser_manager.wait_for_captcha_completion(self.page):
            raise CaptchaError("验证码尚未完成，请手动完成后重试")

    def _first_result_title(self) -> str:
        locator = self.page.locator("td.name a.fz14").first
        if locator.count() == 0:
            return ""
        return locator.inner_text().strip()

    def _current_sort_text(self) -> str:
        locator = self.page.locator("#orderList li.cur").first
        if locator.count() == 0:
            return ""
        return locator.inner_text().strip()

    def _first_visible_locator(self, selectors: list[str], page: Optional[Page] = None) -> Locator:
        target_page = page or self.page
        for selector in selectors:
            locator_group = target_page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=500)
                    return locator
                except Exception:
                    continue
        raise ValidationError(f"未找到页面元素: {selectors[0]}")

    def _click_first_available(self, selectors: list[str], page: Optional[Page] = None) -> bool:
        target_page = page or self.page
        for selector in selectors:
            locator_group = target_page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=500)
                    locator.click()
                    return True
                except Exception:
                    continue
        return False

    def _format_date_range(self, date_from: Optional[str], date_to: Optional[str]) -> str:
        if date_from and date_to:
            return f"{date_from} ~ {date_to}"
        return date_from or date_to or ""

    def _normalize_download_limit(self, num_results: Optional[int], total_results: int) -> int:
        if total_results <= 0:
            return 0
        if num_results is None:
            return total_results
        if num_results <= 0:
            raise ValidationError("下载数量必须大于 0")
        return min(num_results, total_results)

    def _normalize_limit(self, num_results: Optional[int]) -> int:
        if num_results is None:
            return self.config.max_results
        if num_results <= 0:
            raise ValidationError("结果数量必须大于 0")
        return min(num_results, self.config.max_results)
