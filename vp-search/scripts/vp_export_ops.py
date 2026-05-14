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

    def _export_selected_results(
        self,
        query: str,
        batch_index: int,
        output_dir: Path,
        already_at_target: bool = False,
        restore_results_page: bool = True,
    ) -> Dict[str, str]:
        export_started_at = time.perf_counter()
        logger.info(
            "开始导出当前批次: batch=%s, already_at_target=%s, restore_results_page=%s",
            batch_index,
            already_at_target,
            restore_results_page,
        )
        export_page = self._open_export_page(already_at_target=already_at_target)
        try:
            load_started_at = time.perf_counter()
            export_page.wait_for_load_state("domcontentloaded", timeout=self._navigation_timeout_ms())
            logger.debug(
                "导出页加载完成: batch=%s, elapsed_ms=%s",
                batch_index,
                int((time.perf_counter() - load_started_at) * 1000),
            )
            excel_path = self._download_from_export_page(
                export_page=export_page,
                selectors=["li[data-type='excel']", "li[data-type='excel'] a"],
                output_dir=output_dir,
                query=query,
                batch_index=batch_index,
                kind="metadata",
                default_name="vp-export.xls",
            )
            logger.info("元数据下载完成: batch=%s, path=%s", batch_index, excel_path)
            txt_path = self._download_from_export_page(
                export_page=export_page,
                selectors=["li[data-type='abstract']", "li[data-type='abstract'] a"],
                output_dir=output_dir,
                query=query,
                batch_index=batch_index,
                kind="reference",
                default_name="vp-reference.txt",
            )
            logger.info("参考格式下载完成: batch=%s, path=%s", batch_index, txt_path)
            logger.info(
                "当前批次下载阶段完成，准备清理导出页并进入本地处理: batch=%s, elapsed_ms=%s",
                batch_index,
                int((time.perf_counter() - export_started_at) * 1000),
            )
            return {"excel": excel_path, "txt": txt_path}
        finally:
            cleanup_started_at = time.perf_counter()
            self._cleanup_export_page(export_page, restore_results_page=restore_results_page)
            logger.debug(
                "导出页清理完成: batch=%s, restore_results_page=%s, elapsed_ms=%s",
                batch_index,
                restore_results_page,
                int((time.perf_counter() - cleanup_started_at) * 1000),
            )

    def _open_export_page(self, timeout: Optional[float] = None, already_at_target: bool = False) -> Page:
        del already_at_target
        existing_pages = list(self.page.context.pages)
        previous_url = self.page.url
        if not self._click_export_entry():
            raise ValidationError('未找到"导出题录"入口')

        effective_timeout = timeout or self._page_change_timeout_seconds()
        export_page_opened = False
        deadline = time.time() + effective_timeout
        while time.time() < deadline:
            export_page = self._find_ready_export_page(existing_pages, previous_url=previous_url)
            if export_page is not None:
                return export_page
            if self._has_export_page_opened(existing_pages, previous_url=previous_url):
                export_page_opened = True
            time.sleep(self._page_change_poll_interval_seconds())
        if export_page_opened:
            raise TimeoutError("导出页已打开，但关键元素未就绪")
        raise TimeoutError("已点击导出题录，但未检测到导出页打开")

    def _click_export_entry(self) -> bool:
        """点击导出题录入口。"""
        if self._click_first_available(self.EXPORT_ENTRY_SELECTORS):
            return True

        return self._click_export_entry_after_batch_action()

    def _click_export_entry_after_batch_action(self) -> bool:
        """点击批量处理后进入导出入口。

        先通过 Playwright 常规点击触发批量处理，失败则用 JS 兜底逐条激活。
        任一成功后轮询等待导出题录选项出现并点击。
        """
        batch_menu_export_selectors = [
            ".btn-hover-list a.behavior-exporttitle",
            ".articleOperateDown dd a.behavior-exporttitle",
            ".layui-btn-group .btn-hover-list a[data-stype='export']",
            "dl.articleOperateDown dd a:has-text('导出题录')",
            "dl.btn-hover-list dd a:has-text('导出题录')",
        ]
        combined_export_selectors = list(self.EXPORT_ENTRY_SELECTORS) + list(self.EXPORT_ENTRY_MENU_SELECTORS)

        # 常规 Playwright 点击触发批量处理
        activated = self._click_first_available(self.BATCH_ACTION_MENU_SELECTORS)
        if not activated:
            # JS 兜底：绕过 Playwright actionability 检查逐条点击
            for selector in self.BATCH_ACTION_MENU_SELECTORS:
                locator = self.page.locator(selector).first
                if locator.count() == 0:
                    continue
                try:
                    locator.evaluate("(element) => element.click()")
                    activated = True
                    break
                except Exception as exc:
                    logger.debug("JS 兜底触发批量处理失败: selector=%s, error=%s", selector, exc)
                    continue

        if not activated:
            logger.debug("未找到可用的批量处理按钮")
            return False

        # 等待下拉菜单或面板中出现导出题录选项
        deadline = time.time() + self._page_change_timeout_seconds()
        while time.time() < deadline:
            for selector in batch_menu_export_selectors:
                locator = self.page.locator(selector).first
                if locator.count() == 0:
                    continue
                try:
                    locator.wait_for(state="visible", timeout=2000)
                    locator.click(timeout=self._action_timeout_ms())
                    logger.debug("通过批量处理菜单点击导出题录: selector=%s", selector)
                    return True
                except Exception as exc:
                    logger.debug("批量处理菜单导出题录点击失败: selector=%s, error=%s", selector, exc)
                    continue

            if self._has_visible_selector(combined_export_selectors):
                return self._click_first_available(combined_export_selectors)
            if self._has_visible_selector(self.EXPORT_BATCH_DIALOG_SELECTORS):
                return self._click_first_available(self.EXPORT_BATCH_DIALOG_SELECTORS)
            time.sleep(self._action_poll_interval_seconds())
        logger.debug("批量处理已触发，但未找到导出题录或导出全部入口")
        return False

    def _find_ready_export_page(self, existing_pages: list[Page], previous_url: str = "") -> Optional[Page]:
        """返回已经进入导出界面的页面。"""
        for opened_page in self.page.context.pages:
            if opened_page in existing_pages:
                continue
            self._wait_export_page_dom_ready(opened_page)
            if self._is_export_page_ready(opened_page):
                return opened_page

        if previous_url and self.page.url != previous_url:
            self._wait_export_page_dom_ready(self.page)
            if self._is_export_page_ready(self.page):
                return self.page

        if self._is_export_page_ready(self.page):
            return self.page
        return None

    def _has_export_page_opened(self, existing_pages: list[Page], previous_url: str = "") -> bool:
        """判断导出页是否已经打开，但可能仍未就绪。"""
        for opened_page in self.page.context.pages:
            if opened_page not in existing_pages:
                return True
        if previous_url and self.page.url != previous_url:
            return True
        return self._is_export_page_ready(self.page)

    def _wait_export_page_dom_ready(self, export_page: Page) -> None:
        """等待导出页 DOM 基本可用。"""
        try:
            export_page.wait_for_load_state("domcontentloaded", timeout=self._locator_wait_timeout_ms())
        except Exception:
            pass

    def _is_export_page_ready(self, target_page: Page) -> bool:
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

    def _cleanup_export_page(self, export_page: Page, restore_results_page: bool = True) -> None:
        """清理导出页面，避免误关闭结果页。"""
        if export_page is self.page:
            if not restore_results_page:
                return
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

        self._dismiss_batch_modal_if_present()

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
        download_started_at = time.perf_counter()
        export_type = self._infer_export_type(selectors)
        last_error: Optional[ValidationError] = None
        for attempt in range(1, 3):
            download = None
            if export_type and self._should_auto_download_by_export_type(export_type=export_type, kind=kind):
                download = self._capture_export_download_by_option(
                    export_page=export_page,
                    selectors=selectors,
                    kind=kind,
                )
                if download is None:
                    self._select_export_type(
                        export_page=export_page,
                        export_type=export_type,
                        force_reclick=attempt > 1,
                    )
                    self._wait_for_export_type_selected(export_page=export_page, export_type=export_type)
                    download = self._capture_export_download_by_confirm(export_page=export_page, kind=kind)
            else:
                if export_type:
                    self._select_export_type(
                        export_page=export_page,
                        export_type=export_type,
                        force_reclick=attempt > 1,
                    )
                    self._wait_for_export_type_selected(export_page=export_page, export_type=export_type)
                download = self._capture_export_download_by_confirm(export_page=export_page, kind=kind)
            suggested_name = download.suggested_filename or default_name
            try:
                self._validate_export_download_kind(kind=kind, suggested_name=suggested_name)
            except ValidationError as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                logger.warning(
                    "导出结果类型与目标不一致，准备重试: kind=%s, export_type=%s, file=%s, attempt=%s/2",
                    kind,
                    export_type,
                    suggested_name,
                    attempt,
                )
                continue

            file_path = self._build_export_file_path(
                output_dir=output_dir,
                query=query,
                batch_index=batch_index,
                kind=kind,
                suggested_name=suggested_name,
            )
            save_started_at = time.perf_counter()
            download.save_as(str(file_path))
            logger.debug(
                "导出文件已保存: kind=%s, path=%s, save_elapsed_ms=%s, total_elapsed_ms=%s",
                kind,
                file_path,
                int((time.perf_counter() - save_started_at) * 1000),
                int((time.perf_counter() - download_started_at) * 1000),
            )
            return str(file_path)

        if last_error is not None:
            raise last_error
        raise ValidationError(f"导出失败: {kind}")

    def _should_auto_download_by_export_type(self, export_type: str, kind: str) -> bool:
        """判断当前导出类型是否应在切换标签时直接触发下载。"""
        return export_type == "excel" and kind == "metadata"

    def _select_export_type(self, export_page: Page, export_type: str, force_reclick: bool = False) -> None:
        """切换导出类型标签。"""
        selector = f"#dateType li[data-type='{export_type}']"
        locator = export_page.locator(selector).first
        if locator.count() == 0:
            raise ValidationError(f"未找到导出类型标签: {export_type}")

        class_name = (locator.get_attribute("class") or "").strip()
        if "layui-this" in class_name.split() and not force_reclick:
            return
        try:
            locator.click(timeout=self._action_timeout_ms())
        except Exception:
            locator.evaluate("(element) => element.click()")

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
