"""CNKI ?????"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams
from src.utils.playwright_page import click_first_available, disable_checkbox, enable_checkbox, first_visible_locator, set_input_value, wait_for_any_selector
from src.utils.result_output import build_export_file_path
from src.utils.search_timeout import find_env_path, parse_env_file

from browser import BrowserManager
from config import CnkiSearchConfig
from export_processor import ExportResultProcessor
from exceptions import CaptchaError, NavigationStateError, TimeoutError, ValidationError
from progress_store import SearchProgressStore
from result_parser import ResultParser
from utils import build_output_slug

logger = logging.getLogger("cnki_search.interactor")

class CnkiExportMixin:
    """CNKI ?????"""

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
        return build_export_file_path(
            output_dir=output_dir,
            query=query,
            batch_index=batch_index,
            kind=kind,
            suggested_name=suggested_name,
            fallback="cnki",
        )

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
        env_path = find_env_path(Path(__file__))
        env_values = parse_env_file(env_path)

        username = ""
        password = ""
        for key in ["CNKI_USERNAME", "CNKI_USER", "CNKI_ACCOUNT"]:
            username = os.environ.get(key, "").strip() or env_values.get(key, "").strip()
            if username:
                break

        for key in ["CNKI_PASSWORD", "CNKI_PASS"]:
            password = os.environ.get(key, "").strip() or env_values.get(key, "").strip()
            if password:
                break

        if username and password:
            return username, password, env_path

        raise ValidationError(
            f"检测到个人登录弹框，但未在 {env_path} 中找到 CNKI_USERNAME / CNKI_PASSWORD 配置"
        )

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
