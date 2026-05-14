"""万方检索交互实现。"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Page

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams
from src.utils.yearly_export_validation import count_excel_rows

from browser import BrowserManager
from config import WanfangSearchConfig
from exceptions import ValidationError
from export_processor import ExportResultProcessor
from result_parser import ResultParser
from wanfang_export_ops import WanfangExportMixin
from wanfang_form_ops import WanfangFormMixin
from wanfang_navigation_ops import WanfangNavigationMixin
from wanfang_page_ops import WanfangPageMixin
from wanfang_progress_ops import WanfangProgressMixin
from wanfang_public_ops import WanfangPublicMixin
from wanfang_selection_ops import WanfangSelectionMixin
from wanfang_yearly_export_ops import WanfangYearlyExportMixin

logger = logging.getLogger("wanfang_search.interactor")


class WanfangSearchInteractor(
    WanfangYearlyExportMixin,
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
    BATCH_SELECT_MAX_RETRIES = 2
    BROWSER_RESTART_MAX_RETRIES = 2

    def __init__(self, page: Page, config: WanfangSearchConfig, browser_manager: BrowserManager):
        self.page = page
        self.config = config
        self.browser_manager = browser_manager
        self.parser = ResultParser(page)
        self.export_processor = ExportResultProcessor()
        self._results_page_size = self.RESULTS_PER_PAGE_OPTIONS[-1]

    def _get_batch_row_count(self, enriched_file: Path) -> int:
        try:
            return count_excel_rows(enriched_file)
        except Exception:
            return -1

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
        cli_params = {
            "query": query.strip() if query else None,
            "date_from": date_from,
            "date_to": date_to,
            "max_download": max_download,
        }
        if self._should_use_yearly_full_export(date_to=date_to, max_download=max_download):
            return self._run_yearly_advanced_export(cli_params=cli_params, progress_file=progress_file)
        return self.run_advanced_export(
            cli_params=cli_params,
            progress_file=progress_file,
        )

    def _prepare_results_page_for_export(self, planned_download: int, total: int) -> None:
        """导出前设置更合适的每页条数。"""
        del total
        page_size = self._resolve_results_per_page(planned_download)
        self._results_page_size = page_size
        self._set_results_per_page(page_size)
        self._wait_for_results_ready()

    def _resolve_results_per_page(self, planned_download: int) -> int:
        """根据目标数量选择最接近的分页条数。"""
        for option in self.RESULTS_PER_PAGE_OPTIONS:
            if planned_download <= option:
                return option
        return self.RESULTS_PER_PAGE_OPTIONS[-1]

    def _select_batch_for_export(
        self,
        search_params: SearchParams,
        batch_index: int,
        batch_target: int,
        current_page: int,
        current_row_offset: int,
        strict_target: bool,
    ) -> BatchSelectionResult:
        """按批次执行勾选，并在失败时逐级恢复当前批次上下文。"""
        max_attempts = max(int(self.BATCH_SELECT_MAX_RETRIES), 1)
        max_browser_restarts = max(int(self.BROWSER_RESTART_MAX_RETRIES), 0)
        last_error: Optional[BaseException] = None

        for browser_restart_index in range(max_browser_restarts + 1):
            for attempt in range(1, max_attempts + 1):
                try:
                    return BaseAdvancedExportFlow._select_batch_for_export(
                        self,
                        search_params=search_params,
                        batch_index=batch_index,
                        batch_target=batch_target,
                        current_page=current_page,
                        current_row_offset=current_row_offset,
                        strict_target=strict_target,
                    )
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    last_error = exc
                    self._try_clear_selected_results_after_failure()
                    logger.warning(
                        "当前批次勾选失败: batch=%s, attempt=%s/%s, browser_restart=%s/%s, error=%s",
                        batch_index,
                        attempt,
                        max_attempts,
                        browser_restart_index,
                        max_browser_restarts,
                        exc,
                    )
                    if attempt >= max_attempts:
                        break
                    try:
                        self._rebuild_search_results_context_for_batch(
                            search_params=search_params,
                            batch_index=batch_index,
                            target_page=current_page,
                            current_row_offset=current_row_offset,
                        )
                    except Exception as rebuild_exc:
                        last_error = rebuild_exc
                        logger.warning(
                            "当前批次检索上下文重建失败，准备升级为浏览器重试: batch=%s, error=%s",
                            batch_index,
                            rebuild_exc,
                        )
                        break

            if browser_restart_index >= max_browser_restarts:
                break

            self._restart_browser_for_batch_retry(
                search_params=search_params,
                batch_index=batch_index,
                target_page=current_page,
                current_row_offset=current_row_offset,
            )

        if last_error is not None:
            raise last_error
        raise ValidationError(f"批次勾选失败: batch={batch_index}")

    def _try_clear_selected_results_after_failure(self) -> None:
        """勾选失败后尽量清除已选状态，避免污染后续重试。"""
        try:
            self._clear_selected_results()
        except Exception as exc:
            logger.debug("清理失败批次的已选结果时出错: %s", exc)

    def _rebuild_search_results_context_for_batch(
        self,
        search_params: SearchParams,
        batch_index: int,
        target_page: int,
        current_row_offset: int,
    ) -> None:
        """重建当前批次所需的检索结果上下文。"""
        logger.info(
            "准备重建当前批次检索上下文: batch=%s, target_page=%s, row_offset=%s",
            batch_index,
            target_page,
            current_row_offset,
        )
        self._open_advanced_search_page()
        if self.ADVANCED_FORM_READY_SELECTORS:
            self._wait_for_any_selector(list(self.ADVANCED_FORM_READY_SELECTORS))
        self._ensure_captcha_cleared()
        self._fill_advanced_search_form_from_params(search_params)
        self._submit_advanced_search()
        self._wait_for_results_ready()
        self._set_results_per_page(int(getattr(self, "_results_page_size", self.RESULTS_PER_PAGE_OPTIONS[-1])))
        self._wait_for_results_ready()
        self._restore_results_position(target_page)

    def _restart_browser_for_batch_retry(
        self,
        search_params: SearchParams,
        batch_index: int,
        target_page: int,
        current_row_offset: int,
    ) -> None:
        """关闭浏览器并重建当前批次执行上下文。"""
        logger.warning(
            "批次勾选多次失败，准备关闭浏览器重试: batch=%s, target_page=%s, row_offset=%s",
            batch_index,
            target_page,
            current_row_offset,
        )
        self.browser_manager.close()
        self.page = self.browser_manager.start()
        self.parser = ResultParser(self.page)
        self._rebuild_search_results_context_for_batch(
            search_params=search_params,
            batch_index=batch_index,
            target_page=target_page,
            current_row_offset=current_row_offset,
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
