"""???????"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams
from src.utils.playwright_page import click_first_available, disable_checkbox, enable_checkbox, has_visible_selector, set_native_select_value, wait_for_any_selector
from src.utils.result_output import build_export_file_path

from browser import BrowserManager
from config import VpSearchConfig
from exceptions import CaptchaError, TimeoutError, ValidationError
from export_processor import ExportResultProcessor
from progress_store import SearchProgressStore
from result_parser import ResultParser
from utils import build_output_slug

logger = logging.getLogger("vp_search.interactor")

class VpExportMixin:
    """???????"""

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

    def _build_export_file_path(
        self,
        output_dir: Path,
        query: str,
        batch_index: int,
        kind: str,
        suggested_name: str,
    ) -> Path:
        return build_export_file_path(
            output_dir=output_dir,
            query=query,
            batch_index=batch_index,
            kind=kind,
            suggested_name=suggested_name,
            fallback="vp",
            default_suffix=".txt" if kind == "reference" else ".xls",
        )

    def _build_batch_file_name(self, query: str, batch_index: int, kind: str, suffix: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = build_output_slug(query)[:40]
        return f"{timestamp}-{slug}-batch{batch_index:03d}-{kind}{suffix}"

    def _build_summary_file_name(self, query: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = build_output_slug(query)[:40]
        return f"{timestamp}-{slug}-merged.xlsx"
