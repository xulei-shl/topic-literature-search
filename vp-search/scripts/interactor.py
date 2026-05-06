"""维普页面交互。"""

import logging
import re
import time
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from browser import BrowserManager
from config import VpSearchConfig
from exceptions import CaptchaError, TimeoutError, ValidationError
from export_processor import ExportResultProcessor
from progress_store import SearchProgressStore
from result_parser import ResultParser
from utils import build_output_slug

logger = logging.getLogger("vp_search.interactor")


class VpSearchInteractor:
    """负责执行维普高级检索与批量导出。"""

    EXPORT_BATCH_SIZE = 500
    PREFERRED_RESULTS_PAGE_SIZE = 50
    NEXT_PAGE_MAX_RETRIES = 3
    CORE_JOURNAL_TITLES = [
        "北大核心期刊",
        "EI来源期刊",
        "SCIE期刊",
        "CAS来源期刊",
        "CSCD期刊",
        "CSSCI期刊",
    ]
    RESULT_CHECKBOX_SELECTORS = [
        "input[name='selectArticle']",
        "input[data-name='selectArticle']",
        ".search-list input[type='checkbox']",
        ".result-list input[type='checkbox']",
    ]
    RESULT_ROW_CHECKBOX_NAMES = {"selectArticle"}
    RESULT_SELECT_ALL_NAMES = {"selectArticleAll"}
    RESULTS_PAGER_SELECTORS = ["#headerpager", "#footerpager"]
    RESULTS_READY_SELECTORS = [
        "#headerpager",
        "#footerpager",
        "#hidShowTotalCount",
        "span.selected-count",
        ".checked-tip",
        "#selectPageSize",
        "input[name='selectArticleAll']",
    ]
    EXPORT_ENTRY_SELECTORS = ["a.behavior-exporttitle", "a[data-key='export']"]
    EXPORT_PAGE_READY_SELECTORS = [
        "#dateType li[data-type='excel']",
        "#dateType li[data-type='abstract']",
        "li[data-type='excel']",
        "li[data-type='abstract']",
    ]
    EXPORT_CONFIRM_SELECTORS = [
        "#exportbtn",
        "a#exportbtn",
        ".export-op #exportbtn",
        ".export-op a:has-text('导出')",
    ]
    BATCH_ACTION_MENU_SELECTORS = [
        "span.behavior-allDowns",
        ".behavior-allDowns",
        "span.btn-hover:has-text('批量处理')",
        "span:has-text('批量处理')",
    ]

    def __init__(self, page: Page, config: VpSearchConfig, browser_manager: BrowserManager):
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

    def advanced_search(
        self,
        query: Optional[str],
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        core_only: bool = False,
        max_download: Optional[int] = None,
        progress_file: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """执行高级检索并分批导出完整元数据。"""
        cli_params = {
            "query": query.strip() if query else None,
            "date_from": date_from,
            "date_to": date_to,
            "core_only": core_only,
            "max_download": max_download,
        }
        progress_store, resume_data = self._prepare_progress_store(progress_file=progress_file, cli_params=cli_params)
        resolved_params = SearchProgressStore.resolve_search_params(cli_params=cli_params, progress_data=resume_data)

        query = resolved_params["query"].strip()
        date_from = resolved_params["date_from"]
        date_to = resolved_params["date_to"]
        core_only = resolved_params["core_only"]
        max_download = resolved_params["max_download"]

        self._open_advanced_search_page()
        self._wait_for_any_selector(["input[name='advSearchKeywords']", "#basic_beginYear", "#basic_endYear"])
        self._ensure_captcha_cleared()

        self._fill_advanced_search_form(query, date_from, date_to, core_only)
        self._submit_advanced_search()
        self._wait_for_results_ready()
        self._prefer_results_page_size()
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

        batch_count = ceil(planned_download / self.EXPORT_BATCH_SIZE)
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
                batch_target = min(self.EXPORT_BATCH_SIZE, planned_download - exported_total)
                if batch_target <= 0:
                    break

                self._clear_selected_results()
                batch_selection = self._select_batch_results(batch_target, current_row_offset)
                already_at_target = batch_selection.get("already_at_target", False)
                batch_files = self._export_selected_results(query, batch_index, output_dir, already_at_target=already_at_target)
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
            max_download=cli_params.get("max_download"),
        )
        return SearchProgressStore(default_path), None

    def _resolve_output_dir(self, query: str, resume_data: Optional[Dict[str, Any]]) -> Path:
        """返回当前任务应使用的输出目录。"""
        runtime = (resume_data or {}).get("runtime") or {}
        output_dir = runtime.get("output_dir")
        if output_dir:
            resolved_dir = Path(output_dir).resolve()
            resolved_dir.mkdir(parents=True, exist_ok=True)
            return resolved_dir
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
        """恢复到需要续跑的结果页，优先使用页码输入框跳转。"""
        if target_page <= 1:
            return

        summary = self.parser.parse_results_summary()
        current_page = int(summary["current_page"])
        total_pages = int(summary.get("total_pages") or 0)
        if current_page > target_page:
            raise ValidationError(f"当前结果页码大于目标恢复页码: {current_page} > {target_page}")
        if total_pages and target_page > total_pages:
            raise ValidationError(f"目标恢复页码超出总页数: {target_page} > {total_pages}")

        while current_page < target_page:
            moved = self._jump_to_results_page(target_page)
            if not moved:
                page_link = self._find_resume_target_page_link(current_page=current_page, target_page=target_page)
                if page_link is not None:
                    moved = self._goto_results_page_by_link(page_link)
            if not moved:
                moved = self._goto_next_results_page()
            if not moved:
                raise ValidationError(f"未能恢复到目标页码: {target_page}")
            next_page = int(self.parser.parse_results_summary()["current_page"])
            if next_page <= current_page:
                raise ValidationError(f"恢复页码未前进: {current_page} -> {next_page}")
            current_page = next_page

    def _find_resume_skip_controls(self) -> tuple[Optional[Locator], Optional[Locator]]:
        """查找结果页“到第 X 页”输入框与确认按钮。"""
        if not self.page or not hasattr(self.page, "locator"):
            return None, None

        for pager_selector in self.RESULTS_PAGER_SELECTORS:
            input_locator = self.page.locator(f"{pager_selector} .layui-laypage-skip input.layui-input").first
            button_locator = self.page.locator(f"{pager_selector} .layui-laypage-skip .layui-laypage-btn").first
            if input_locator.count() > 0 and button_locator.count() > 0:
                return input_locator, button_locator

        input_locator = self.page.locator(".layui-laypage-skip input.layui-input").first
        button_locator = self.page.locator(".layui-laypage-skip .layui-laypage-btn").first
        if input_locator.count() > 0 and button_locator.count() > 0:
            return input_locator, button_locator
        return None, None

    def _click_results_page_link(self, page_link: Locator, attempt: int) -> None:
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
        except Exception as exc:
            logger.debug("常规点击页码失败，尝试使用 JS 点击: %s", exc)
            page_link.evaluate("(element) => element.click()")

    def _click_skip_page_button(self, button_locator: Locator, attempt: int) -> None:
        """执行结果页页码输入跳转确认。"""
        try:
            button_locator.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("跳页确认按钮滚动到可视区域失败: %s", exc)

        if attempt < self.NEXT_PAGE_MAX_RETRIES:
            button_locator.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            return

        try:
            button_locator.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        except Exception as exc:
            logger.debug("常规点击跳页确认按钮失败，尝试使用 JS 点击: %s", exc)
            button_locator.evaluate("(element) => element.click()")

    def _jump_to_results_page(self, target_page: int) -> bool:
        """通过分页输入框跳转到目标页。"""
        input_locator, button_locator = self._find_resume_skip_controls()
        if input_locator is None or button_locator is None:
            return False

        summary = self.parser.parse_results_summary()
        previous_current_page = int(summary["current_page"])
        previous_url = self.page.url
        previous_page = summary["page"]
        previous_title = self._first_result_title()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            try:
                input_locator.fill(str(target_page))
                self._click_skip_page_button(button_locator, attempt)
                self._wait_for_results_changed(previous_url, previous_page, previous_title)
                return int(self.parser.parse_results_summary()["current_page"]) > previous_current_page
            except Exception as exc:
                last_error = exc
                try:
                    if self._has_results_state_changed(previous_url, previous_page, previous_title):
                        return int(self.parser.parse_results_summary()["current_page"]) > previous_current_page
                except Exception:
                    pass
                logger.debug(
                    "结果页输入框跳转失败，准备重试: attempt=%s/%s, target_page=%s, error=%s",
                    attempt,
                    self.NEXT_PAGE_MAX_RETRIES,
                    target_page,
                    exc,
                )
                self._dismiss_confirm_dialog_if_present()
                self._ensure_captcha_cleared()
                if attempt < self.NEXT_PAGE_MAX_RETRIES:
                    time.sleep(self._page_change_poll_interval_seconds())

        logger.debug("结果页输入框跳转最终失败: target_page=%s, error=%s", target_page, last_error)
        return False

    def _goto_results_page_by_link(self, page_link: Locator) -> bool:
        """点击数字页码并等待结果页完成跳转。"""
        summary = self.parser.parse_results_summary()
        previous_current_page = int(summary["current_page"])
        previous_url = self.page.url
        previous_page = summary["page"]
        previous_title = self._first_result_title()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            try:
                self._click_results_page_link(page_link, attempt)
                self._wait_for_results_changed(previous_url, previous_page, previous_title)
                return int(self.parser.parse_results_summary()["current_page"]) > previous_current_page
            except Exception as exc:
                last_error = exc
                try:
                    if self._has_results_state_changed(previous_url, previous_page, previous_title):
                        return int(self.parser.parse_results_summary()["current_page"]) > previous_current_page
                except Exception:
                    pass
                logger.debug(
                    "结果页数字页跳转失败，准备重试: attempt=%s/%s, error=%s",
                    attempt,
                    self.NEXT_PAGE_MAX_RETRIES,
                    exc,
                )
                self._dismiss_confirm_dialog_if_present()
                self._ensure_captcha_cleared()
                if attempt < self.NEXT_PAGE_MAX_RETRIES:
                    time.sleep(self._page_change_poll_interval_seconds())

        logger.debug("结果页数字页跳转最终失败: error=%s", last_error)
        return False

    def _find_resume_target_page_link(self, current_page: int, target_page: int) -> Optional[Locator]:
        """查找恢复续跑时可直接点击的目标数字页。"""
        if not self.page or not hasattr(self.page, "locator"):
            return None

        candidate_link: Optional[Locator] = None
        candidate_page = current_page

        for pager_selector in self.RESULTS_PAGER_SELECTORS:
            page_links = self.page.locator(f"{pager_selector} a[data-page]")
            for index in range(page_links.count()):
                link = page_links.nth(index)
                try:
                    text = link.inner_text().strip()
                except Exception:
                    continue
                if not text.isdigit():
                    continue

                raw_page = (link.get_attribute("data-page") or text).strip()
                if not raw_page.isdigit():
                    continue

                page_number = int(raw_page)
                if page_number <= current_page or page_number > target_page:
                    continue

                class_name = (link.get_attribute("class") or "").strip()
                if "layui-disabled" in class_name or "disabled" in class_name or "layui-laypage-curr" in class_name:
                    continue

                if page_number == target_page:
                    return link

                if page_number > candidate_page:
                    candidate_page = page_number
                    candidate_link = link

        return candidate_link

    def _save_progress_snapshot(
        self,
        progress_store: SearchProgressStore,
        status: str,
        query: str,
        date_from: Optional[str],
        date_to: Optional[str],
        core_only: bool,
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

    def _fill_advanced_search_form(
        self,
        query: str,
        date_from: Optional[str],
        date_to: Optional[str],
        core_only: bool,
    ) -> None:
        self._set_advanced_condition(0, "题名或关键词", query)
        self._set_advanced_condition(1, "摘要", query, logic="OR")
        if date_from:
            self._set_native_select_value("#basic_beginYear", date_from)
        if date_to:
            self._set_native_select_value("#basic_endYear", date_to)

        if core_only:
            self._disable_checkbox("input[name='basic_journalRange'][title='全部期刊']")
            for title in self.CORE_JOURNAL_TITLES:
                self._enable_checkbox(f"input[name='basic_journalRange'][title='{title}']")

    def _set_advanced_condition(self, row_index: int, field_title: str, query: str, logic: str = "") -> None:
        row = self._get_advanced_condition_row(row_index)
        if logic:
            self._select_dropdown_option(row, logic, display_text={"OR": "或", "AND": "与", "NOT": "非"}[logic])
        self._select_dropdown_option(row, field_title, display_text=field_title)

        query_input = self.page.locator("input[name='advSearchKeywords']").nth(row_index)
        if query_input.count() == 0:
            raise ValidationError(f"未找到第 {row_index + 1} 条检索词输入框")
        query_input.fill(query)

    def _get_advanced_condition_row(self, row_index: int) -> Locator:
        query_input = self.page.locator("input[name='advSearchKeywords']").nth(row_index)
        if query_input.count() == 0:
            raise ValidationError(f"未找到第 {row_index + 1} 条高级检索条件")
        for xpath in [
            "xpath=ancestor::li[1]",
            "xpath=ancestor::dd[1]",
            "xpath=ancestor::div[contains(@class,'input-group')][1]",
        ]:
            row = query_input.locator(xpath)
            if row.count() > 0:
                return row
        return query_input

    def _select_dropdown_option(self, root: Locator, option_value: str, display_text: str) -> None:
        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            dropdowns = root.locator(".layui-form-select")
            for index in range(dropdowns.count()):
                dropdown = dropdowns.nth(index)
                option = dropdown.locator(f"dd[lay-value='{option_value}']").first
                if option.count() == 0:
                    option = dropdown.locator("dd").filter(has_text=display_text).first
                if option.count() == 0:
                    continue
                try:
                    dropdown.click()
                    option.wait_for(state="visible", timeout=self._locator_wait_timeout_ms())
                    option.click(force=True)
                    return
                except Exception as exc:
                    logger.debug("等待下拉项失败: %s", exc)
            time.sleep(self._action_poll_interval_seconds())
        raise ValidationError(f"高级检索下拉项不存在: {display_text}")

    def _open_advanced_search_page(self) -> None:
        self.browser_manager.restore_session()
        self._ensure_captcha_cleared()

        opened = self._click_first_available(
            [
                "a[href='/Qikan/Search/Advance?from=index']",
                "a[href*='/Qikan/Search/Advance']",
                "a:has-text('高级检索')",
            ]
        )
        if opened:
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=self._navigation_timeout_ms())
            except Exception as exc:
                logger.debug("点击高级检索入口后等待页面稳定失败，准备直接校验页面状态: %s", exc)
            if self._is_advanced_search_page(self.page):
                self.browser_manager._page = self.page
                self.parser = ResultParser(self.page)
                return

        self.page.goto(self.config.advanced_search_url, timeout=self._navigation_timeout_ms())
        self.page.wait_for_load_state("domcontentloaded")
        if not self._is_advanced_search_page(self.page):
            raise ValidationError("打开维普高级检索页面失败")

        self.browser_manager._page = self.page
        self.parser = ResultParser(self.page)

    def _is_advanced_search_page(self, target_page: Page) -> bool:
        for selector in ["input[name='advSearchKeywords']", "#basic_beginYear", "#basic_endYear", ".behavior-advancesearch"]:
            locator_group = target_page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=self._locator_wait_timeout_ms())
                    return True
                except Exception:
                    continue
        return False

    def _submit_advanced_search(self) -> None:
        if not self._click_first_available(["button.behavior-advancesearch", ".behavior-advancesearch", "button:has-text('检索')"]):
            raise ValidationError("未找到高级检索提交按钮")
        self._dismiss_confirm_dialog_if_present()

    def _prefer_results_page_size(self) -> None:
        page_size_link = self._find_preferred_results_page_size_link()
        if page_size_link is None:
            logger.debug("未找到每页显示数量入口，保持当前分页大小: target=%s", self.PREFERRED_RESULTS_PAGE_SIZE)
            return

        class_name = page_size_link.get_attribute("class") or ""
        if "active" in class_name:
            logger.debug("每页显示数量已是目标值，无需切换: target=%s", self.PREFERRED_RESULTS_PAGE_SIZE)
            return

        previous_page = ""
        try:
            previous_page = self.parser.parse_results_summary()["page"]
        except Exception:
            previous_page = ""
        previous_row_count = self._result_checkbox_locator().count()

        try:
            logger.debug(
                "准备切换每页显示数量: target=%s, previous_page=%s, previous_row_count=%s",
                self.PREFERRED_RESULTS_PAGE_SIZE,
                previous_page,
                previous_row_count,
            )
            page_size_link.click()
            self._wait_for_results_page_size_applied(page_size_link, previous_page, previous_row_count)
            logger.debug("每页显示数量切换完成: target=%s", self.PREFERRED_RESULTS_PAGE_SIZE)
        except Exception as exc:
            logger.debug("切换每页显示数量失败: target=%s, error=%s", self.PREFERRED_RESULTS_PAGE_SIZE, exc)

    def _find_preferred_results_page_size_link(self) -> Optional[Locator]:
        """查找“每页 50 条”入口，兼容不同分页容器实现。"""
        page_size_selectors = [
            f"#selectPageSize a[data-count='{self.PREFERRED_RESULTS_PAGE_SIZE}']",
            f"#selectPageSize [data-count='{self.PREFERRED_RESULTS_PAGE_SIZE}']",
            f"[data-count='{self.PREFERRED_RESULTS_PAGE_SIZE}']",
        ]
        for selector in page_size_selectors:
            locator_group = self.page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=self._locator_wait_timeout_ms())
                    return locator
                except Exception:
                    continue
        return None

    def _wait_for_results_page_size_applied(
        self,
        page_size_link: Locator,
        previous_page: str,
        previous_row_count: int,
    ) -> None:
        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            self._ensure_captcha_cleared()
            class_name = page_size_link.get_attribute("class") or ""
            if "active" in class_name:
                return

            current_row_count = self._result_checkbox_locator().count()
            if previous_row_count and current_row_count and current_row_count != previous_row_count:
                return

            try:
                current_page = self.parser.parse_results_summary()["page"]
            except Exception:
                current_page = ""
            if previous_page and current_page and current_page != previous_page:
                return

            time.sleep(self._page_change_poll_interval_seconds())
        raise TimeoutError("等待每页显示数量切换超时")

    def _clear_selected_results(self) -> None:
        clear_link = self.page.locator("span.selected-count a[title='清空已选文章']").first
        if clear_link.count() == 0:
            clear_link = self.page.locator("span.selected-count a").nth(1)
        if clear_link.count() == 0:
            return
        try:
            clear_link.click()
            self._dismiss_confirm_dialog_if_present()
            time.sleep(self._action_poll_interval_seconds())
        except Exception as exc:
            logger.debug("清除已选文献失败: %s", exc)

    def _select_batch_results(self, export_limit: int, row_offset: int) -> Dict[str, Any]:
        remaining = export_limit
        selected_count = 0
        current_row_offset = row_offset
        current_row_count = 0

        while remaining > 0:
            self._wait_for_results_ready()
            time.sleep(0.5)
            checkbox_items = self._current_page_checkbox_items()
            current_row_count = len(checkbox_items)
            if current_row_count == 0:
                logger.debug("当前页未获取到复选框，可能页面未加载完成，重试")
                time.sleep(1)
                checkbox_items = self._current_page_checkbox_items()
                current_row_count = len(checkbox_items)
            summary = self.parser.parse_results_summary()
            current_page = summary.get("current_page", "unknown")
            logger.debug(
                "批量勾选循环: export_limit=%s, selected_count=%s, remaining=%s, row_offset=%s, row_count=%s, current_page=%s",
                export_limit,
                selected_count,
                remaining,
                current_row_offset,
                current_row_count,
                current_page,
            )
            if current_row_count == 0:
                logger.debug("当前结果页未找到可勾选记录，结束本轮勾选")
                break

            if current_row_offset >= current_row_count:
                if remaining <= 0:
                    logger.debug("已达成目标数量，无需翻页: remaining=%s", remaining)
                    break
                logger.debug(
                    "当前页剩余记录不足，准备翻页继续: row_offset=%s, row_count=%s",
                    current_row_offset,
                    current_row_count,
                )
                if not self._goto_next_results_page():
                    logger.debug("翻页失败或不存在下一页，结束本轮勾选")
                    break
                current_row_offset = 0
                continue

            page_target_count = min(current_row_count - current_row_offset, remaining)
            selected_before_page = self._extract_selected_count(0)
            self._select_rows_on_current_page(
                checkbox_items=checkbox_items,
                row_offset=current_row_offset,
                page_target_count=page_target_count,
                row_count=current_row_count,
                selected_before_page=selected_before_page,
            )
            page_actual_selected = self._extract_selected_count(selected_count + page_target_count)
            selected_count = page_actual_selected
            remaining = export_limit - selected_count
            current_row_offset += page_target_count
            logger.debug(
                "当前页勾选完成: page_target_count=%s, selected_count=%s, remaining=%s, next_row_offset=%s, page_actual_selected=%s",
                page_target_count,
                selected_count,
                remaining,
                current_row_offset,
                page_actual_selected,
            )

            if remaining <= 0:
                break
            if current_row_offset < current_row_count:
                continue
            if not self._goto_next_results_page():
                logger.debug("当前页已勾满但翻页失败或不存在下一页，结束本轮勾选")
                break
            current_row_offset = 0

        if selected_count <= 0:
            raise ValidationError("结果页未选中任何文献，无法导出")

        already_at_target = export_limit - selected_count <= 0

        return {
            "selected_count": selected_count,
            "next_row_offset": current_row_offset,
            "page_row_count": current_row_count,
            "already_at_target": already_at_target,
        }

    def _select_rows_on_current_page(
        self,
        checkbox_items: list[Locator],
        row_offset: int,
        page_target_count: int,
        row_count: int,
        selected_before_page: int,
    ) -> None:
        if row_offset == 0 and page_target_count == row_count:
            logger.debug(
                "当前页满足整页勾选条件，准备尝试页级全选: row_count=%s, selected_before_page=%s",
                row_count,
                selected_before_page,
            )
            if self._try_select_all_on_current_page(
                expected_increase=page_target_count,
                selected_before_page=selected_before_page,
            ):
                logger.debug("当前页页级全选校验通过")
                return

        logger.debug(
            "当前页改为逐条勾选: row_offset=%s, page_target_count=%s, row_count=%s",
            row_offset,
            page_target_count,
            row_count,
        )

        if row_offset == 0 and page_target_count < row_count:
            logger.debug(
                "部分页目标，跳过页级全选，仅逐条勾选: page_target_count=%s, row_count=%s",
                page_target_count,
                row_count,
            )
            current_page_selected = self._extract_selected_count(0)
            if current_page_selected >= row_count * 0.8:
                logger.debug(
                    "页面已有全选状态，需要清除后再逐条勾选: current=%s, row_count=%s",
                    current_page_selected,
                    row_count,
                )
                self._clear_page_selection(checkbox_items)

        self._select_rows_incrementally(
            checkbox_items=checkbox_items,
            row_offset=row_offset,
            page_target_count=page_target_count,
            selected_before_page=0,
        )

    def _try_select_all_on_current_page(self, expected_increase: int, selected_before_page: int) -> bool:
        """尝试对当前页执行页级全选，并校验已选数量增量是否符合预期。"""
        selectors = [
            "input[name='selectArticleAll']",
            "input[type='checkbox'][name='selectArticleAll']",
            "#selectAll",
            ".select-all input",
            "th input[type='checkbox']",
            "thead input[type='checkbox']",
        ]
        select_all = None
        for sel in selectors:
            loc = self.page.locator(sel).first
            if loc.count() > 0:
                select_all = loc
                logger.debug("找到全选控件: selector=%s", sel)
                break

        if select_all is None or select_all.count() == 0:
            logger.debug("当前页不存在页级全选控件，回退逐条勾选")
            return False

        self._ensure_checkbox_checked(select_all, selector="input[name='selectArticleAll']")
        selected_after_page = self._extract_selected_count(0)
        if selected_after_page <= selected_before_page:
            selected_after_page = self._extract_selected_count(selected_before_page + expected_increase)
        actual_increase = max(selected_after_page - selected_before_page, 0)
        logger.debug(
            "页级全选后数量校验: selected_before=%s, selected_after=%s, expected_increase=%s, actual_increase=%s",
            selected_before_page,
            selected_after_page,
            expected_increase,
            actual_increase,
        )
        if actual_increase == expected_increase:
            return True
        if actual_increase == 0:
            selected_after_page = self._extract_selected_count(selected_before_page + expected_increase)
            actual_increase = max(selected_after_page - selected_before_page, 0)
            if actual_increase >= expected_increase:
                logger.debug("页级全选后数量校验(兜底): actual_increase=%s >= expected_increase=%s", actual_increase, expected_increase)
                return True

        logger.warning(
            "结果页全选数量异常，回退逐条勾选: expected_increase=%s, actual_increase=%s",
            expected_increase,
            actual_increase,
        )
        self._disable_checkbox("input[name='selectArticleAll']")
        selected_after_rollback = self._extract_selected_count(selected_before_page)
        logger.debug(
            "结果页全选回退后数量校验: selected_before=%s, selected_after_rollback=%s",
            selected_before_page,
            selected_after_rollback,
        )
        return False

    def _select_rows_incrementally(
        self,
        checkbox_items: list[Locator],
        row_offset: int,
        page_target_count: int,
        selected_before_page: int,
    ) -> None:
        """逐条勾选当前页记录，页内不再逐条读取已选数量。"""
        del selected_before_page
        for index in range(row_offset, row_offset + page_target_count):
            if index >= len(checkbox_items):
                logger.debug(
                    "复选框索引超出范围: index=%s, len=%s",
                    index,
                    len(checkbox_items),
                )
                break
            checkbox = checkbox_items[index]

            max_retries = 3
            for retry in range(max_retries):
                try:
                    self._ensure_checkbox_checked(checkbox, selector=f"result_checkbox[{index}]")
                    break
                except Exception as exc:
                    if retry < max_retries - 1:
                        logger.debug(
                            "复选框勾选失败，准备重试: index=%s, attempt=%s/%s, error=%s",
                            index,
                            retry + 1,
                            max_retries,
                            exc,
                        )
                        self.page.evaluate("window.scrollBy(0, 50)")
                        time.sleep(0.3)
                    else:
                        raise

    def _rollback_excess_selection(
        self,
        target_count: int,
        checkbox_items: list[Locator],
        max_uncheck_count: int,
    ) -> None:
        """回滚多余的选中项，只保留目标数量。"""
        current_selected = self._extract_selected_count(0)
        if current_selected <= target_count:
            logger.debug("当前选中数量未超过目标，无需回滚: current=%s, target=%s", current_selected, target_count)
            return

        excess_count = current_selected - target_count
        logger.debug(
            "开始回滚多余的选中项: current_selected=%s, target_count=%s, excess_count=%s",
            current_selected,
            target_count,
            excess_count,
        )

        uncheck_count = 0
        for index in range(len(checkbox_items)):
            if uncheck_count >= excess_count or uncheck_count >= max_uncheck_count:
                break
            checkbox = checkbox_items[index]
            if self._is_checkbox_checked(checkbox, None):
                try:
                    self._ensure_checkbox_unchecked(checkbox, selector=f"result_checkbox[{index}]")
                    uncheck_count += 1
                    current_selected = self._extract_selected_count(0)
                    if current_selected <= target_count:
                        logger.debug("回滚完成，当前选中数量已达到目标: %s", current_selected)
                        break
                except Exception as exc:
                    logger.debug("取消选中失败: index=%s, error=%s", index, exc)

        logger.debug("回滚完成: 取消选中数量=%s, 当前选中=%s", uncheck_count, self._extract_selected_count(0))

    def _clear_page_selection(self, checkbox_items: list[Locator]) -> None:
        """清除当前页所有选中项。"""
        for checkbox in checkbox_items:
            if self._is_checkbox_checked(checkbox, None):
                try:
                    click_target = self._resolve_checkbox_click_target(checkbox)
                    if click_target is not None:
                        click_target.evaluate(
                            """
                            (el) => {
                                el.classList.remove('layui-form-checked');
                                const event = new MouseEvent('click', { bubbles: true, cancelable: true });
                                el.dispatchEvent(event);
                            }
                            """
                        )
                        if not self._is_checkbox_checked(checkbox, click_target):
                            continue
                    checkbox.uncheck(force=True, timeout=self._action_timeout_ms())
                except Exception as exc:
                    logger.debug("清除选中项失败: error=%s", exc)

        time.sleep(0.2)
        logger.debug("页面选中状态已清除: 当前选中=%s", self._extract_selected_count(0))

    def _result_checkbox_locator(self) -> Locator:
        for selector in self.RESULT_CHECKBOX_SELECTORS:
            locator = self.page.locator(selector)
            if locator.count() > 0:
                return locator
        return self.page.locator("input[name='selectArticle']")

    def _current_page_checkbox_items(self) -> list[Locator]:
        """返回当前页可操作的结果复选框列表。"""
        checkbox_locator = self._result_checkbox_locator()
        total_count = checkbox_locator.count()
        summary = self.parser.parse_results_summary()
        current_page = summary.get("current_page", 1)
        page_size = self._current_results_page_size()
        paged_items = self._slice_checkbox_items_by_current_page(checkbox_locator=checkbox_locator, total_count=total_count)
        if paged_items:
            filtered_items = self._filter_result_row_checkbox_items(paged_items)
            if len(filtered_items) < page_size:
                try:
                    current_page_number = int(current_page or 1)
                except Exception:
                    current_page_number = 1
                raw_index = max((current_page_number - 1) * page_size, 0) + len(paged_items)
                while raw_index < total_count and len(filtered_items) < page_size:
                    candidate = checkbox_locator.nth(raw_index)
                    if self._is_result_row_checkbox(candidate):
                        filtered_items.append(candidate)
                    raw_index += 1
            logger.debug(
                "获取当前页复选框: total_count=%s, filtered_count=%s, current_page=%s, page_size=%s",
                total_count,
                len(filtered_items),
                current_page,
                page_size,
            )
            logger.debug("按当前页码切分复选框成功: total_count=%s, page_items=%s", total_count, len(filtered_items))
            return filtered_items

        checkbox_items = self._filter_result_row_checkboxes(checkbox_locator=checkbox_locator, total_count=total_count)
        logger.debug(
            "获取当前页复选框: total_count=%s, filtered_count=%s, current_page=%s, page_size=%s",
            total_count,
            len(checkbox_items),
            current_page,
            page_size,
        )

        visible_items: list[Locator] = []
        for checkbox in checkbox_items:
            click_target = self._resolve_checkbox_click_target(checkbox)
            if click_target is not None:
                if self._is_locator_visible(click_target):
                    visible_items.append(checkbox)
                continue
            if self._is_locator_visible(checkbox):
                visible_items.append(checkbox)
        logger.debug("筛选当前页可见复选框: total_count=%s, visible_count=%s", len(checkbox_items), len(visible_items))
        return visible_items

    def _filter_result_row_checkboxes(self, checkbox_locator: Locator, total_count: int) -> list[Locator]:
        """过滤掉页级全选等非结果行复选框。"""
        checkbox_items = [checkbox_locator.nth(index) for index in range(total_count)]
        return self._filter_result_row_checkbox_items(checkbox_items)

    def _filter_result_row_checkbox_items(self, checkbox_items: list[Locator]) -> list[Locator]:
        """过滤当前候选集合中的页级全选控件。"""
        filtered_items: list[Locator] = []
        skipped_indices: list[int] = []
        for index, checkbox in enumerate(checkbox_items):
            if self._is_result_row_checkbox(checkbox):
                filtered_items.append(checkbox)
                continue
            skipped_indices.append(index)
        if skipped_indices:
            logger.debug(
                "跳过非结果行复选框: skipped_count=%s, sample_indices=%s",
                len(skipped_indices),
                skipped_indices[:5],
            )
        return filtered_items

    def _is_result_row_checkbox(self, checkbox: Locator) -> bool:
        """判断复选框是否属于结果行，而非页级全选。"""
        try:
            name = (checkbox.get_attribute("name") or "").strip()
        except Exception:
            name = ""
        try:
            data_name = (checkbox.get_attribute("data-name") or "").strip()
        except Exception:
            data_name = ""

        if name in self.RESULT_SELECT_ALL_NAMES or data_name in self.RESULT_SELECT_ALL_NAMES:
            return False
        if name in self.RESULT_ROW_CHECKBOX_NAMES or data_name in self.RESULT_ROW_CHECKBOX_NAMES:
            return True
        return True

    def _slice_checkbox_items_by_current_page(self, checkbox_locator: Locator, total_count: int) -> list[Locator]:
        """按当前页码与每页条数切出当前页复选框。"""
        if total_count <= 0:
            return []

        try:
            current_page = int(self.parser.parse_results_summary().get("current_page") or 1)
        except Exception:
            current_page = 1

        page_size = self._current_results_page_size()
        if page_size <= 0 or total_count <= page_size:
            return []

        start_index = max((current_page - 1) * page_size, 0)
        end_index = min(start_index + page_size, total_count)
        if start_index >= total_count or end_index <= start_index:
            logger.debug(
                "按页码切分复选框失败，准备回退可见性筛选: current_page=%s, page_size=%s, total_count=%s",
                current_page,
                page_size,
                total_count,
            )
            return []

        return [checkbox_locator.nth(index) for index in range(start_index, end_index)]

    def _current_results_page_size(self) -> int:
        """读取当前结果页激活的每页条数。"""
        selector_candidates = [
            "#selectPageSize a.active[data-count]",
            "#selectPageSize [data-count].active",
            "#selectPageSize [data-count][class*='active']",
        ]
        for selector in selector_candidates:
            locator = self.page.locator(selector).first
            try:
                if locator.count() == 0:
                    continue
                raw_count = (locator.get_attribute("data-count") or "").strip()
                if raw_count.isdigit():
                    return int(raw_count)
            except Exception:
                continue
        return self.PREFERRED_RESULTS_PAGE_SIZE

    def _is_locator_visible(self, locator: Optional[Locator]) -> bool:
        """判断定位器是否处于可见状态。"""
        if locator is None:
            return False
        try:
            if locator.count() == 0:
                return False
        except Exception:
            return False
        try:
            return bool(locator.is_visible())
        except Exception:
            return False

    def _resolve_checkbox_click_target(self, checkbox: Locator) -> Optional[Locator]:
        for selector in [
            "xpath=following-sibling::div[contains(@class,'layui-form-checkbox')][1]",
            "xpath=../div[contains(@class,'layui-form-checkbox')][1]",
            "xpath=ancestor::dd[1]/dd[2]/div[contains(@class,'layui-form-checkbox')][1]",
            ".layui-form-checkbox",
        ]:
            try:
                target = checkbox.locator(selector).first
                if target.count() > 0:
                    return target
            except Exception:
                continue
        try:
            parent_dd = checkbox.locator("xpath=ancestor::dd[contains(@class,'sel')][1]").first
            if parent_dd.count() > 0:
                target = parent_dd.locator("div.layui-form-checkbox").first
                if target.count() > 0:
                    return target
        except Exception:
            pass
        return None

    def _is_checkbox_checked(self, checkbox: Locator, click_target: Optional[Locator] = None) -> bool:
        try:
            if checkbox.is_checked():
                return True
        except Exception:
            pass

        target = click_target or self._resolve_checkbox_click_target(checkbox)
        if target is None:
            return False

        try:
            class_name = target.get_attribute("class") or ""
        except Exception:
            return False
        return "layui-form-checked" in class_name or "layui-this" in class_name

    def _ensure_checkbox_checked(self, checkbox: Locator, selector: str = "") -> None:
        click_target = self._resolve_checkbox_click_target(checkbox)
        if self._is_checkbox_checked(checkbox, click_target):
            return

        if click_target is not None:
            try:
                click_target.evaluate(
                    """
                    (el) => {
                        el.classList.add('layui-form-checked');
                        const event = new MouseEvent('click', { bubbles: true, cancelable: true });
                        el.dispatchEvent(event);
                    }
                    """
                )
                if self._is_checkbox_checked(checkbox, click_target):
                    return
            except Exception as exc:
                logger.debug("JS 点击 Layui 复选框失败: selector=%s, error=%s", selector or "<locator>", exc)

            try:
                click_target.scroll_into_view_if_needed(timeout=3000)
            except Exception as exc:
                logger.debug("Layui 复选框滚动到可视区域失败: selector=%s, error=%s", selector or "<locator>", exc)

            try:
                click_target.click(force=True, timeout=self._action_timeout_ms())
                if self._is_checkbox_checked(checkbox, click_target):
                    return
            except Exception as exc:
                logger.debug("点击 Layui 复选框失败，准备回退到原生 input: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            checkbox.scroll_into_view_if_needed(timeout=3000)
        except Exception as exc:
            logger.debug("复选框滚动到可视区域失败: selector=%s, error=%s", selector or "<locator>", exc)

        try:
            checkbox.check(force=True, timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("常规勾选复选框失败，尝试使用 JS 兜底: selector=%s, error=%s", selector or "<locator>", exc)

        if self._is_checkbox_checked(checkbox, click_target):
            return
        raise TimeoutError(f"未能完成复选框勾选: {selector or '<locator>'}")

    def _prepare_next_batch_cursor(self, batch_selection: Dict[str, Any]) -> int:
        next_row_offset = batch_selection["next_row_offset"]
        page_row_count = batch_selection["page_row_count"]
        if next_row_offset < page_row_count:
            return next_row_offset
        if self._goto_next_results_page():
            return 0
        return next_row_offset

    def _click_next_page_link(self, next_link: Locator, attempt: int) -> None:
        try:
            next_link.scroll_into_view_if_needed(timeout=self._action_timeout_ms())
        except Exception as exc:
            logger.debug("翻页按钮滚动到可视区域失败: %s", exc)

        if attempt < self.NEXT_PAGE_MAX_RETRIES:
            next_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            return
        try:
            next_link.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        except Exception as exc:
            logger.debug("常规点击“下一页”失败，尝试使用 JS 点击: %s", exc)
            next_link.evaluate("(element) => element.click()")

    def _goto_next_results_page(self) -> bool:
        previous_url = self.page.url
        summary = self.parser.parse_results_summary()
        previous_page = summary["page"]
        previous_current_page = int(summary.get("current_page") or 1)
        previous_title = self._first_result_title()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.NEXT_PAGE_MAX_RETRIES + 1):
            next_link = self._find_next_page_link()
            if next_link is None:
                logger.debug("未找到下一页控件: previous_page=%s, previous_url=%s", previous_page, previous_url)
                return False

            class_name = next_link.get_attribute("class") or ""
            data_page = next_link.get_attribute("data-page") or ""
            target_page = int(data_page) if data_page.isdigit() else (previous_current_page + 1)
            try:
                next_text = next_link.inner_text().strip()
            except Exception:
                next_text = ""
            logger.debug(
                "准备执行结果页翻页: attempt=%s/%s, previous_page=%s, next_text=%s, data_page=%s, class=%s",
                attempt,
                self.NEXT_PAGE_MAX_RETRIES,
                previous_page,
                next_text,
                data_page,
                class_name,
            )
            if "layui-disabled" in class_name or "disabled" in class_name:
                logger.debug("下一页控件处于禁用状态，停止翻页: previous_page=%s", previous_page)
                return False

            try:
                self._click_next_page_link(next_link, attempt)
                self._wait_for_results_page_advanced(
                    previous_current_page=previous_current_page,
                    target_page=target_page,
                    previous_url=previous_url,
                    previous_page=previous_page,
                    previous_title=previous_title,
                )
                current_summary = self.parser.parse_results_summary()
                current_page = current_summary["page"]
                current_current_page = int(current_summary.get("current_page") or 0)
                logger.debug(
                    "结果页翻页完成: previous_page=%s, current_page=%s, target_page=%s",
                    previous_page,
                    current_page,
                    target_page,
                )
                if current_current_page < target_page:
                    raise TimeoutError(f"结果页页码未推进到目标页: current={current_current_page}, target={target_page}")
                return True
            except Exception as exc:
                last_error = exc
                try:
                    if self._is_results_page_advanced(previous_current_page=previous_current_page, target_page=target_page):
                        return True
                except Exception:
                    pass
                logger.warning(
                    "结果页翻页失败，准备重试: attempt=%s/%s, error=%s",
                    attempt,
                    self.NEXT_PAGE_MAX_RETRIES,
                    exc,
                )
                self._dismiss_confirm_dialog_if_present()
                self._ensure_captcha_cleared()
                if attempt < self.NEXT_PAGE_MAX_RETRIES:
                    time.sleep(self._page_change_poll_interval_seconds())
        raise TimeoutError(f"结果页翻页失败，已重试 {self.NEXT_PAGE_MAX_RETRIES} 次: {last_error}")

    def _has_results_state_changed(self, previous_url: str, previous_page: str, previous_title: str) -> bool:
        current_url = self.page.url
        current_page = self.parser.parse_results_summary()["page"]
        current_title = self._first_result_title()
        return any([current_url != previous_url, current_page != previous_page, current_title != previous_title])

    def _is_results_page_advanced(self, previous_current_page: int, target_page: int) -> bool:
        """判断结果页页码是否已经推进到目标页。"""
        try:
            current_current_page = int(self.parser.parse_results_summary().get("current_page") or 0)
        except Exception:
            return False
        return current_current_page >= target_page and current_current_page > previous_current_page

    def _wait_for_results_page_advanced(
        self,
        previous_current_page: int,
        target_page: int,
        previous_url: str,
        previous_page: str,
        previous_title: str,
        timeout: Optional[float] = None,
    ) -> None:
        """等待结果页页码真正推进到目标页。"""
        deadline = time.time() + (timeout or self._page_change_timeout_seconds())
        logger.debug(
            "开始等待结果页翻页完成: previous_page=%s, target_page=%s, previous_url=%s",
            previous_page,
            target_page,
            previous_url,
        )
        while time.time() < deadline:
            self._ensure_captcha_cleared()
            try:
                if self._is_results_page_advanced(previous_current_page=previous_current_page, target_page=target_page):
                    current_page = self.parser.parse_results_summary()["page"]
                    logger.debug(
                        "检测到结果页页码已推进: previous_page=%s, current_page=%s, target_page=%s",
                        previous_page,
                        current_page,
                        target_page,
                    )
                    return
                if self._has_results_state_changed(previous_url, previous_page, previous_title):
                    current_page = self.parser.parse_results_summary()["page"]
                    logger.debug(
                        "检测到结果页状态变化但页码未推进，继续等待: previous_page=%s, current_page=%s, target_page=%s",
                        previous_page,
                        current_page,
                        target_page,
                    )
            except Exception as exc:
                logger.debug("等待结果页翻页完成时状态刷新中，继续等待: %s", exc)
            time.sleep(self._page_change_poll_interval_seconds())
        try:
            current_page = self.parser.parse_results_summary()["page"]
        except Exception:
            current_page = ""
        raise TimeoutError(f"等待翻页完成超时: previous_page={previous_page}, current_page={current_page}, target_page={target_page}")

    def _wait_for_results_changed(
        self,
        previous_url: str,
        previous_page: str,
        previous_title: str,
        timeout: Optional[float] = None,
    ) -> None:
        deadline = time.time() + (timeout or self._page_change_timeout_seconds())
        logger.debug(
            "开始等待结果页变化: previous_page=%s, previous_url=%s, previous_title=%s",
            previous_page,
            previous_url,
            previous_title,
        )
        while time.time() < deadline:
            self._ensure_captcha_cleared()
            try:
                if self._has_results_state_changed(previous_url, previous_page, previous_title):
                    try:
                        current_page = self.parser.parse_results_summary()["page"]
                    except Exception:
                        current_page = ""
                    logger.debug("检测到结果页变化: previous_page=%s, current_page=%s", previous_page, current_page)
                    return
            except Exception as exc:
                logger.debug("结果页状态刷新中，继续等待: %s", exc)
            time.sleep(self._page_change_poll_interval_seconds())
        try:
            current_page = self.parser.parse_results_summary()["page"]
        except Exception:
            current_page = ""
        logger.debug(
            "等待结果页变化超时: previous_page=%s, current_page=%s, current_url=%s",
            previous_page,
            current_page,
            self.page.url,
        )
        raise TimeoutError("等待翻页完成超时")

    def _find_next_page_link(self) -> Optional[Locator]:
        pager_selectors = []
        for pager_selector in self.RESULTS_PAGER_SELECTORS:
            pager_selectors.extend(
                [
                    f"{pager_selector} .layui-laypage-next",
                    f"{pager_selector} a[data-page]",
                    f"{pager_selector} a",
                    f"{pager_selector} button",
                    f"{pager_selector} span",
                ]
            )
        pager_selectors.extend(["a.layui-laypage-next", "a", "button", "span"])

        for selector in pager_selectors:
            locator_group = self.page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    text = locator.inner_text().strip()
                except Exception:
                    text = ""
                if selector.endswith("a[data-page]") and text and text.isdigit():
                    continue
                if not self._is_next_page_control(selector=selector, text=text):
                    continue
                class_name = (locator.get_attribute("class") or "").strip()
                if "layui-disabled" in class_name or "disabled" in class_name:
                    continue
                return locator
        return None

    def _is_next_page_control(self, selector: str, text: str) -> bool:
        """判断候选节点是否为“下一页”控件。"""
        if selector.endswith(".layui-laypage-next"):
            return True

        normalized_text = (text or "").replace(" ", "").strip()
        if normalized_text in {"下一页", "下页", ">", ">>"}:
            return True
        return "下一页" in normalized_text

    def _export_selected_results(self, query: str, batch_index: int, output_dir: Path, already_at_target: bool = False) -> Dict[str, str]:
        export_page = self._open_export_page(already_at_target=already_at_target)
        try:
            export_page.wait_for_load_state("domcontentloaded", timeout=self._navigation_timeout_ms())
            excel_path = self._download_from_export_page(
                export_page=export_page,
                selectors=["li[data-type='excel']", "li[data-type='excel'] a"],
                output_dir=output_dir,
                query=query,
                batch_index=batch_index,
                kind="metadata",
                default_name="vp-export.xls",
            )
            txt_path = self._download_from_export_page(
                export_page=export_page,
                selectors=["li[data-type='abstract']", "li[data-type='abstract'] a"],
                output_dir=output_dir,
                query=query,
                batch_index=batch_index,
                kind="reference",
                default_name="vp-reference.txt",
            )
            return {"excel": excel_path, "txt": txt_path}
        finally:
            self._cleanup_export_page(export_page)

    def _open_export_page(self, timeout: Optional[float] = None, already_at_target: bool = False) -> Page:
        existing_pages = list(self.page.context.pages)
        if not self._click_export_entry():
            raise ValidationError('未找到"导出题录"入口')

        effective_timeout = 5.0 if already_at_target else (timeout or self._page_change_timeout_seconds())
        deadline = time.time() + effective_timeout
        while time.time() < deadline:
            export_page = self._find_ready_export_page(existing_pages)
            if export_page is not None:
                return export_page
            time.sleep(self._page_change_poll_interval_seconds())
        raise TimeoutError("等待导出页面打开超时")

    def _click_export_entry(self) -> bool:
        """点击导出题录入口。"""
        if self._click_first_available(self.EXPORT_ENTRY_SELECTORS):
            return True

        if self._click_first_available(self.BATCH_ACTION_MENU_SELECTORS):
            time.sleep(self._action_poll_interval_seconds())
            return self._click_first_available(self.EXPORT_ENTRY_SELECTORS)
        return False

    def _find_ready_export_page(self, existing_pages: list[Page]) -> Optional[Page]:
        """返回已经进入导出界面的页面。"""
        for opened_page in self.page.context.pages:
            if opened_page in existing_pages:
                continue
            if self._is_export_page(opened_page):
                return opened_page

        if self._is_export_page(self.page):
            return self.page
        return None

    def _is_export_page(self, target_page: Page) -> bool:
        """判断当前页面是否已经进入导出界面。"""
        for selector in self.EXPORT_PAGE_READY_SELECTORS:
            locator = target_page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                locator.wait_for(state="visible", timeout=self._locator_wait_timeout_ms())
                return True
            except Exception:
                continue
        return False

    def _cleanup_export_page(self, export_page: Page) -> None:
        """清理导出页面，避免误关闭结果页。"""
        if export_page is self.page:
            try:
                export_page.go_back(timeout=self._navigation_timeout_ms())
                export_page.wait_for_load_state("domcontentloaded", timeout=self._navigation_timeout_ms())
                self._wait_for_results_ready()
            except Exception as exc:
                logger.debug("导出后返回结果页失败: %s", exc)
            return

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
        download = self._capture_export_download_by_option(export_page=export_page, selectors=selectors, kind=kind)
        if download is None:
            export_type = self._infer_export_type(selectors)
            if export_type:
                self._wait_for_export_type_selected(export_page=export_page, export_type=export_type)
            download = self._capture_export_download_by_confirm(export_page=export_page, kind=kind)

        suggested_name = download.suggested_filename or default_name
        self._validate_export_download_kind(kind=kind, suggested_name=suggested_name)
        file_path = self._build_export_file_path(
            output_dir=output_dir,
            query=query,
            batch_index=batch_index,
            kind=kind,
            suggested_name=suggested_name,
        )
        download.save_as(str(file_path))
        return str(file_path)

    def _capture_export_download_by_option(self, export_page: Page, selectors: list[str], kind: str):
        """优先捕获点击导出类型时触发的自动下载。"""
        try:
            with export_page.expect_download(timeout=self._export_option_download_probe_timeout_ms()) as download_info:
                if not self._click_first_available(selectors, page=export_page):
                    raise ValidationError(f"未找到导出选项: {kind}")
        except PlaywrightTimeoutError:
            return None
        return download_info.value

    def _capture_export_download_by_confirm(self, export_page: Page, kind: str):
        """在需要确认按钮的页面上触发下载。"""
        trigger_selectors = self.EXPORT_CONFIRM_SELECTORS
        if not self._has_visible_selector(trigger_selectors, page=export_page):
            raise ValidationError(f"未找到导出按钮: {kind}")

        with export_page.expect_download(timeout=self._download_timeout_ms()) as download_info:
            if not self._click_first_available(trigger_selectors, page=export_page):
                raise ValidationError(f"未找到导出按钮: {kind}")
        return download_info.value

    def _infer_export_type(self, selectors: list[str]) -> Optional[str]:
        """从导出选择器中推断当前导出类型。"""
        for selector in selectors:
            match = re.search(r"data-type=['\"]([^'\"]+)['\"]", selector)
            if match:
                return match.group(1)
        return None

    def _wait_for_export_type_selected(self, export_page: Page, export_type: str) -> None:
        """等待导出类型进入选中态，避免下载到错误格式。"""
        selector = f"#dateType li[data-type='{export_type}']"
        locator = export_page.locator(selector).first
        if locator.count() == 0:
            raise ValidationError(f"未找到导出类型标签: {export_type}")

        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            class_name = (locator.get_attribute("class") or "").strip()
            if "layui-this" in class_name.split():
                return
            time.sleep(self._action_poll_interval_seconds())
        raise ValidationError(f"导出类型未切换成功: {export_type}")

    def _validate_export_download_kind(self, kind: str, suggested_name: str) -> None:
        """校验下载文件类型与导出目标是否一致。"""
        suffix = Path(suggested_name).suffix.lower()
        if kind == "metadata" and suffix not in {".xls", ".xlsx", ".xlsm"}:
            raise ValidationError(f"Excel 导出未生效，当前下载文件为: {suggested_name}")
        if kind == "reference" and suffix and suffix != ".txt":
            raise ValidationError(f"参考文献导出格式异常，当前下载文件为: {suggested_name}")

    def _has_visible_selector(self, selectors: list[str], page: Optional[Page] = None) -> bool:
        """判断任一选择器是否存在可见元素。"""
        target_page = page or self.page
        for selector in selectors:
            locator_group = target_page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=self._locator_wait_timeout_ms())
                    return True
                except Exception:
                    continue
        return False

    def _build_export_file_path(
        self,
        output_dir: Path,
        query: str,
        batch_index: int,
        kind: str,
        suggested_name: str,
    ) -> Path:
        suffix = Path(suggested_name).suffix or (".txt" if kind == "reference" else ".xls")
        return output_dir / self._build_batch_file_name(query, batch_index, kind, suffix)

    def _build_batch_file_name(self, query: str, batch_index: int, kind: str, suffix: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = build_output_slug(query)[:40]
        return f"{timestamp}-{slug}-batch{batch_index:03d}-{kind}{suffix}"

    def _build_summary_file_name(self, query: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = build_output_slug(query)[:40]
        return f"{timestamp}-{slug}-merged.xlsx"

    def _set_native_select_value(self, selector: str, value: str) -> None:
        locator = self.page.locator(selector).first
        if locator.count() == 0:
            raise ValidationError(f"未找到年份选择器: {selector}")
        locator.evaluate(
            """
            (element, selectedValue) => {
                element.value = selectedValue;
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            value,
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
                try:
                    checkbox.uncheck(force=True)
                except Exception:
                    pass
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

    def _extract_selected_count(self, default_value: int) -> int:
        selectors = [
            "span[data-topcount='topcount']",
            ".checked-tip .mark-number",
            ".selected-count",
            "span.selected-count",
            ".selected-count span",
            "#selectCount",
            "span[data-count]",
        ]
        for selector in selectors:
            locator = self.page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                text = locator.inner_text().replace(",", "").replace("，", "").strip()
                import re
                numbers = re.findall(r'\d+', text)
                if numbers:
                    logger.debug(
                        "获取选中数量成功: selector=%s, text=%s, count=%s",
                        selector,
                        text,
                        numbers[0],
                    )
                    return int(numbers[0])
            except Exception as exc:
                logger.debug("获取选中数量失败: selector=%s, error=%s", selector, exc)
                continue
        logger.debug("未能获取选中数量，使用默认值: %s", default_value)
        return default_value

    def _wait_for_results_ready(self) -> None:
        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            self._ensure_captcha_cleared()
            if self._is_results_page_ready():
                return
            if self.page.locator(".no-data, .empty, .none-data").count() > 0:
                return
            time.sleep(self._page_poll_interval_seconds())
        raise TimeoutError("等待结果页超时")

    def _is_results_page_ready(self) -> bool:
        for selector in self.RESULTS_READY_SELECTORS:
            if self.page.locator(selector).count() > 0:
                return True
        return self._result_checkbox_locator().count() > 0

    def _dismiss_confirm_dialog_if_present(self) -> None:
        confirm_button = self.page.locator(".layui-layer-btn0").first
        if confirm_button.count() == 0:
            return
        try:
            confirm_button.click()
        except Exception:
            pass

    def _ensure_captcha_cleared(self) -> None:
        if not self.browser_manager.is_captcha_visible(self.page):
            return
        if not self.browser_manager.wait_for_captcha_completion(self.page):
            raise CaptchaError("验证码尚未完成，请手动完成后重试")

    def _first_result_title(self) -> str:
        for selector in [
            ".search-list a[title]",
            ".result-list a[title]",
            ".article-item a[title]",
            "h3 a",
        ]:
            locator = self.page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                return locator.inner_text().strip()
            except Exception:
                continue
        return ""

    def _click_first_available(self, selectors: list[str], page: Optional[Page] = None) -> bool:
        target_page = page or self.page
        for selector in selectors:
            locator_group = target_page.locator(selector)
            if locator_group.count() == 0:
                continue
            for index in range(locator_group.count()):
                locator = locator_group.nth(index)
                try:
                    locator.wait_for(state="visible", timeout=self._locator_wait_timeout_ms())
                    locator.click()
                    return True
                except Exception:
                    continue
        return False

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
                        locator.wait_for(state="visible", timeout=self._locator_wait_timeout_ms())
                        return
                    except Exception:
                        continue
            time.sleep(self._page_poll_interval_seconds())
        raise TimeoutError(f"等待页面元素超时: {selectors[0]}")

    def _action_timeout_ms(self) -> int:
        timeout_seconds = getattr(self.config, "action_timeout", self.config.page_timeout)
        return int(timeout_seconds * 1000)

    def _navigation_timeout_ms(self) -> int:
        timeout_seconds = getattr(self.config, "navigation_timeout", self.config.page_timeout)
        return int(timeout_seconds * 1000)

    def _download_timeout_ms(self) -> int:
        timeout_seconds = getattr(self.config, "page_change_timeout", self.config.page_timeout)
        return int(timeout_seconds * 1000)

    def _export_option_download_probe_timeout_ms(self) -> int:
        """返回探测导出选项是否会自动下载的短超时时间。"""
        return max(1000, int(self._action_timeout_ms() / 2))

    def _page_change_timeout_seconds(self) -> float:
        timeout_seconds = getattr(self.config, "page_change_timeout", self.config.page_timeout)
        return float(timeout_seconds)

    def _locator_wait_timeout_ms(self) -> int:
        return int(self._action_timeout_ms() / 60)

    def _action_poll_interval_seconds(self) -> float:
        timeout_seconds = getattr(self.config, "action_timeout", self.config.page_timeout)
        return float(timeout_seconds) / 100

    def _page_poll_interval_seconds(self) -> float:
        timeout_seconds = getattr(self.config, "page_timeout", 30)
        return float(timeout_seconds) / 120

    def _page_change_poll_interval_seconds(self) -> float:
        return self._page_change_timeout_seconds() / 240

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
