"""万方导出操作 Mixin。"""

import logging
import time
from pathlib import Path
from typing import Dict

from playwright.sync_api import Locator, Page

from src.utils.result_output import build_export_file_path

from exceptions import ValidationError

logger = logging.getLogger("wanfang_search.interactor")


class WanfangExportMixin:
    """万方导出操作。"""

    EXPORT_TAB_ID_MAP = {
        "自定义格式": "div#tab-m5",
        "参考文献": "div#tab-m2",
    }

    def _export_selected_results_for_batch(
        self,
        query: str,
        batch_index: int,
        output_dir: Path,
        batch_selection: Dict[str, int],
    ) -> Dict[str, str]:
        """导出当前批次的 XLS 与 TXT。"""
        del batch_selection
        self._cache_progress_page_context()
        export_page = self._open_export_page()
        try:
            excel_path = self._export_xls(export_page, output_dir, query, batch_index)
            txt_path = self._export_txt(export_page, output_dir, query, batch_index)
            return {"excel": excel_path, "txt": txt_path}
        finally:
            if export_page is not self.page:
                try:
                    export_page.close()
                except Exception:
                    pass

    def _open_export_page(self) -> Page:
        """打开批量引用新页面。"""
        existing_pages = list(self.page.context.pages)
        export_button = self.page.locator("span.export-btn").filter(has_text="批量引用").first
        if export_button.count() == 0:
            raise ValidationError("未找到批量引用按钮")

        clicked = False
        popup_timeout_ms = min(self._action_timeout_ms(), 5000)
        try:
            with self.page.expect_popup(timeout=popup_timeout_ms) as popup_info:
                export_button.click(timeout=self._action_timeout_ms(), no_wait_after=True)
                clicked = True
            export_page = popup_info.value
            export_page.wait_for_load_state("domcontentloaded", timeout=self._action_timeout_ms())
            export_page.wait_for_timeout(500)
            logger.debug(
                "导出页打开: 新页面URL=%s, 标签数=%s",
                export_page.url,
                len(self.page.context.pages),
            )
            if self._is_export_page(export_page):
                return export_page
            logger.debug("导出页内容未加载完成，继续等待")
        except Exception as exc:
            logger.debug("批量引用弹窗捕获失败，改用轮询兜底: %s", exc)

        if not clicked:
            export_button.click(timeout=self._action_timeout_ms(), no_wait_after=True)

        deadline = time.time() + self.config.page_timeout
        while time.time() < deadline:
            for opened_page in self.page.context.pages:
                if opened_page not in existing_pages:
                    opened_page.wait_for_load_state("domcontentloaded", timeout=self._action_timeout_ms())
                    opened_page.wait_for_timeout(500)
                    if self._is_export_page(opened_page):
                        logger.debug(
                            "导出页打开: 新页面URL=%s, 标签数=%s",
                            opened_page.url,
                            len(self.page.context.pages),
                        )
                        return opened_page
                    logger.debug("页面URL=%s 但非导出页", opened_page.url)
            if self._is_export_page(self.page):
                logger.debug("导出页打开: 复用当前标签页 URL=%s", self.page.url)
                return self.page
            time.sleep(0.3)
        raise ValidationError("未能打开万方批量引用页面")

    def _export_xls(
        self,
        export_page: Page,
        output_dir: Path,
        query: str,
        batch_index: int,
    ) -> str:
        """导出 XLS 元数据。"""
        self._switch_export_tab(export_page, "", "自定义格式")
        self._select_list_format(export_page)
        return self._download_from_export_page(
            export_page=export_page,
            selector="div.export-text-action",
            button_text="导出XLS",
            output_dir=output_dir,
            query=query,
            batch_index=batch_index,
            kind="metadata",
            default_name="wanfang-export.xls",
        )

    def _export_txt(
        self,
        export_page: Page,
        output_dir: Path,
        query: str,
        batch_index: int,
    ) -> str:
        """导出参考文献 TXT。"""
        self._switch_export_tab(export_page, "", "参考文献")
        return self._download_from_export_page(
            export_page=export_page,
            selector="div.export-text-action",
            button_text="导出TXT",
            output_dir=output_dir,
            query=query,
            batch_index=batch_index,
            kind="reference",
            default_name="wanfang-reference.txt",
            default_suffix=".txt",
        )

    def _switch_export_tab(self, export_page: Page, selector: str, tab_name: str) -> None:
        """切换导出页标签。"""
        tab = self._find_export_tab(export_page, tab_name)
        if (tab is None or tab.count() == 0) and selector:
            tab = export_page.locator(selector).first
        if tab is None or tab.count() == 0:
            html = export_page.content()[:2000]
            logger.debug("可用导出tab: %s", html)
            raise ValidationError(f"未找到导出标签: {tab_name}")

        if self._is_export_tab_active(tab) and self._is_export_tab_ready(export_page, tab_name):
            logger.debug("Tab 已就绪，跳过切换: 目标tab=%s", tab_name)
            return

        tab.first.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        deadline = time.time() + float(self.config.action_timeout)
        while time.time() < deadline:
            class_attr = self._read_locator_class(tab)
            active = self._class_has_token(class_attr, "active")
            ready = self._is_export_tab_ready(export_page, tab_name)
            logger.debug(
                "Tab 切换: 目标tab=%s, class=%s, active=%s, ready=%s",
                tab_name,
                class_attr,
                active,
                ready,
            )
            if ready and (active or tab_name == "自定义格式"):
                return
            time.sleep(0.15)
        raise ValidationError(f"导出标签切换失败: {tab_name}")

    def _find_export_tab(self, export_page: Page, tab_name: str) -> Locator | None:
        """按已知结构优先定位导出标签。"""
        selector = self.EXPORT_TAB_ID_MAP.get(tab_name, "")
        if selector:
            tab = export_page.locator(selector).first
            if tab.count() > 0:
                return tab
        return self._find_export_tab_by_text(export_page, tab_name)

    def _find_export_tab_by_text(self, export_page: Page, tab_name: str) -> Locator | None:
        """按文本查找导出页标签。"""
        tab_text_map = {
            "自定义格式": "自定义格式",
            "参考文献": "参考文献",
        }
        target_text = tab_text_map.get(tab_name, tab_name)
        tab = export_page.locator("div.export-tabs_item").filter(has_text=target_text).first
        if tab.count() > 0:
            return tab
        return export_page.locator("div[class*='tabs']").filter(has_text=target_text).first

    def _select_list_format(self, export_page: Page) -> None:
        """选择列表格式单选项。"""
        labels = export_page.locator("label.ivu-radio-wrapper")
        for index in range(labels.count()):
            label = labels.nth(index)
            try:
                text = label.inner_text().strip()
            except Exception:
                continue
            if "列表格式" not in text:
                continue
            class_name = label.get_attribute("class") or ""
            if "ivu-radio-wrapper-checked" not in class_name:
                label.click(timeout=self._action_timeout_ms(), no_wait_after=True)
                time.sleep(0.2)
            return
        raise ValidationError("未找到“列表格式”选项")

    def _download_from_export_page(
        self,
        export_page: Page,
        selector: str,
        button_text: str,
        output_dir: Path,
        query: str,
        batch_index: int,
        kind: str,
        default_name: str,
        default_suffix: str = ".xls",
    ) -> str:
        """在导出页触发下载。"""
        button = export_page.locator(selector).filter(has_text=button_text).first
        if button.count() == 0:
            raise ValidationError(f"未找到导出按钮: {button_text}")

        with export_page.expect_download(timeout=45000) as download_info:
            button.click(timeout=self._action_timeout_ms())

        download = download_info.value
        file_path = build_export_file_path(
            output_dir=output_dir,
            query=query,
            batch_index=batch_index,
            kind=kind,
            suggested_name=download.suggested_filename or default_name,
            fallback="wanfang",
            default_suffix=default_suffix,
        )
        download.save_as(str(file_path))
        logger.debug(
            "下载触发: 选择器=%s, 建议文件名=%s, 实际路径=%s",
            selector,
            download.suggested_filename or default_name,
            file_path,
        )
        return str(file_path)

    def _is_export_page(self, target_page: Page) -> bool:
        """判断当前页面是否已进入导出页。"""
        marker_selectors = (
            "div.export-text-action",
            "div.export-tabs_item",
            "div.custom-item-radio",
            "div#tab-m2",
            "div#tab-m5",
        )
        for selector in marker_selectors:
            locator = target_page.locator(selector).first
            try:
                if locator.count() > 0 and locator.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _is_export_tab_ready(self, export_page: Page, tab_name: str) -> bool:
        """判断导出页目标标签是否已就绪。"""
        button_text = "导出TXT" if tab_name == "参考文献" else "导出XLS"
        button = export_page.locator("div.export-text-action").filter(has_text=button_text).first
        if button.count() > 0 and button.is_visible():
            return True
        if tab_name == "自定义格式":
            radio_group = export_page.locator("div.custom-item-radio").first
            return radio_group.count() > 0 and radio_group.is_visible()
        return False

    def _is_export_tab_active(self, tab: Locator) -> bool:
        """判断目标导出标签是否处于激活态。"""
        return self._class_has_token(self._read_locator_class(tab), "active")

    def _read_locator_class(self, locator: Locator) -> str:
        """安全读取定位器 class。"""
        try:
            return (locator.get_attribute("class") or "").strip()
        except Exception:
            return ""

    def _class_has_token(self, class_attr: str, token: str) -> bool:
        """判断 class 是否包含目标 token。"""
        class_tokens = {item.strip().lower() for item in class_attr.split() if item.strip()}
        return token in class_tokens or f"is-{token}" in class_tokens
