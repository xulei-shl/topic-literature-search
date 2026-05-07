"""万方检索交互实现。"""

from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Page

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams

from browser import BrowserManager
from config import WanfangSearchConfig
from export_processor import ExportResultProcessor
from result_parser import ResultParser
from wanfang_export_ops import WanfangExportMixin
from wanfang_form_ops import WanfangFormMixin
from wanfang_navigation_ops import WanfangNavigationMixin
from wanfang_page_ops import WanfangPageMixin
from wanfang_progress_ops import WanfangProgressMixin
from wanfang_public_ops import WanfangPublicMixin
from wanfang_selection_ops import WanfangSelectionMixin


class WanfangSearchInteractor(
    WanfangPublicMixin,
    WanfangFormMixin,
    WanfangSelectionMixin,
    WanfangNavigationMixin,
    WanfangPageMixin,
    WanfangExportMixin,
    WanfangProgressMixin,
    BaseAdvancedExportFlow,
):
    """负责执行万方高级检索与批量导出。"""

    ADVANCED_FORM_READY_SELECTORS = (
        "input.ivu-input.ivu-input-default",
        "span.submit-btn",
    )
    EXPORT_BATCH_SIZE = 500
    NEXT_PAGE_MAX_RETRIES = 3
    NEXT_PAGE_RETRY_DELAY = 1
    RESULTS_PER_PAGE_OPTIONS = (20, 30, 50)

    def __init__(self, page: Page, config: WanfangSearchConfig, browser_manager: BrowserManager):
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
        max_download: Optional[int] = None,
        progress_file: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """执行高级检索并分批导出完整元数据。"""
        return self.run_advanced_export(
            cli_params={
                "query": query.strip() if query else None,
                "date_from": date_from,
                "date_to": date_to,
                "max_download": max_download,
            },
            progress_file=progress_file,
        )

    def _prepare_results_page_for_export(self, planned_download: int, total: int) -> None:
        """导出前设置更合适的每页条数。"""
        del total
        page_size = self._resolve_results_per_page(planned_download)
        self._set_results_per_page(page_size)
        self._wait_for_results_ready()

    def _resolve_results_per_page(self, planned_download: int) -> int:
        """根据目标数量选择最接近的分页条数。"""
        for option in self.RESULTS_PER_PAGE_OPTIONS:
            if planned_download <= option:
                return option
        return self.RESULTS_PER_PAGE_OPTIONS[-1]

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
