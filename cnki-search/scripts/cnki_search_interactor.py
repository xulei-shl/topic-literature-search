"""CNKI 检索交互实现。"""

from typing import Any, Dict, Optional
from pathlib import Path

from playwright.sync_api import Page

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams

from browser import BrowserManager
from config import CnkiSearchConfig
from export_processor import ExportResultProcessor
from result_parser import ResultParser

from cnki_export_ops import CnkiExportMixin
from cnki_form_ops import CnkiFormMixin
from cnki_navigation_ops import CnkiNavigationMixin
from cnki_page_ops import CnkiPageMixin
from cnki_progress_ops import CnkiProgressMixin
from cnki_public_ops import CnkiPublicMixin
from cnki_selection_ops import CnkiSelectionMixin
from cnki_yearly_export_ops import CnkiYearlyExportMixin

class CnkiSearchInteractor(
    CnkiYearlyExportMixin,
    CnkiPublicMixin,
    CnkiFormMixin,
    CnkiSelectionMixin,
    CnkiNavigationMixin,
    CnkiPageMixin,
    CnkiExportMixin,
    CnkiProgressMixin,
    BaseAdvancedExportFlow,
):
    """负责执行 CNKI 各类交互。"""

    ADVANCED_FORM_READY_SELECTORS = (
        "#gradetxt input",
        "input[placeholder='结束年']",
        "input.btn-search",
    )
    EXPORT_BATCH_SIZE = 500
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
        if self._should_use_yearly_full_export(date_to=date_to, max_download=max_download):
            return self._run_yearly_advanced_export(cli_params=cli_params, progress_file=progress_file)
        return self.run_advanced_export(
            cli_params=cli_params,
            progress_file=progress_file,
        )

    def _fill_advanced_search_form_from_params(self, search_params: SearchParams) -> None:
        """按共享骨架要求填写高级检索表单。"""
        self._fill_advanced_search_form(
            query=str(search_params["query"]).strip(),
            date_from=search_params.get("date_from"),
            date_to=search_params.get("date_to"),
            core_only=bool(search_params.get("core_only")),
            include_no_fulltext=bool(search_params.get("include_no_fulltext")),
        )

    def _prepare_results_page_for_export(self, planned_download: int, total: int) -> None:
        """统一在导出前设置每页条数。"""
        self._set_results_per_page(min(planned_download, self.EXPORT_BATCH_SIZE), total)

    def _export_selected_results_for_batch(
        self,
        query: str,
        batch_index: int,
        output_dir: Path,
        batch_selection: BatchSelectionResult,
    ) -> Dict[str, str]:
        """按共享骨架导出当前批次。"""
        del batch_selection
        return self._export_selected_results(query, batch_index, output_dir)

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
        current_page: int,
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
            include_no_fulltext=bool(search_params.get("include_no_fulltext")),
            max_download=search_params.get("max_download"),
            output_dir=output_dir,
            planned_download=planned_download,
            batch_count=batch_count,
            exported_total=exported_total,
            exported_batches=exported_batches,
            next_batch_index=next_batch_index,
            current_page=current_page,
            current_row_offset=current_row_offset,
            enriched_batch_files=enriched_batch_files,
            final_file_path=final_file_path,
            error=error,
        )
