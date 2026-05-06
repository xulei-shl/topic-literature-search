"""维普检索交互实现。"""

from typing import Any, Dict, Optional
from pathlib import Path

from playwright.sync_api import Page

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams

from browser import BrowserManager
from config import VpSearchConfig
from export_processor import ExportResultProcessor
from result_parser import ResultParser

from vp_export_ops import VpExportMixin
from vp_checkbox_list_ops import VpCheckboxListMixin
from vp_form_ops import VpFormMixin
from vp_navigation_ops import VpNavigationMixin
from vp_page_ops import VpPageMixin
from vp_page_size_ops import VpPageSizeMixin
from vp_progress_ops import VpProgressMixin
from vp_selection_ops import VpSelectionMixin

class VpSearchInteractor(
    VpFormMixin,
    VpPageSizeMixin,
    VpCheckboxListMixin,
    VpSelectionMixin,
    VpNavigationMixin,
    VpPageMixin,
    VpExportMixin,
    VpProgressMixin,
    BaseAdvancedExportFlow,
):
    """负责执行维普高级检索与批量导出。"""

    EXPORT_BATCH_SIZE = 500
    ADVANCED_FORM_READY_SELECTORS = (
        "input[name='advSearchKeywords']",
        "#basic_beginYear",
        "#basic_endYear",
    )
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
        return self.run_advanced_export(
            cli_params={
            "query": query.strip() if query else None,
            "date_from": date_from,
            "date_to": date_to,
            "core_only": core_only,
            "max_download": max_download,
            },
            progress_file=progress_file,
        )

    def _fill_advanced_search_form_from_params(self, search_params: SearchParams) -> None:
        """按共享骨架要求填写高级检索表单。"""
        self._fill_advanced_search_form(
            query=str(search_params["query"]).strip(),
            date_from=search_params.get("date_from"),
            date_to=search_params.get("date_to"),
            core_only=bool(search_params.get("core_only")),
        )

    def _prepare_results_page_for_export(self, planned_download: int, total: int) -> None:
        """导出前优先切换到更大的分页数量。"""
        del planned_download, total
        self._prefer_results_page_size()
        self._wait_for_results_ready()

    def _export_selected_results_for_batch(
        self,
        query: str,
        batch_index: int,
        output_dir: Path,
        batch_selection: BatchSelectionResult,
    ) -> Dict[str, str]:
        """按共享骨架导出当前批次。"""
        return self._export_selected_results(
            query,
            batch_index,
            output_dir,
            already_at_target=bool(batch_selection.get("already_at_target")),
        )

    def _save_progress_snapshot_for_flow(
        self,
        progress_store,
        status: str,
        search_params: SearchParams,
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
        """将共享骨架参数转给站点快照逻辑。"""
        self._save_progress_snapshot(
            progress_store=progress_store,
            status=status,
            query=str(search_params["query"]).strip(),
            date_from=search_params.get("date_from"),
            date_to=search_params.get("date_to"),
            core_only=bool(search_params.get("core_only")),
            max_download=search_params.get("max_download"),
            output_dir=output_dir,
            planned_download=planned_download,
            batch_count=batch_count,
            exported_total=exported_total,
            exported_batches=exported_batches,
            next_batch_index=next_batch_index,
            current_row_offset=current_row_offset,
            enriched_batch_files=enriched_batch_files,
            final_file_path=final_file_path,
            error=error,
        )
