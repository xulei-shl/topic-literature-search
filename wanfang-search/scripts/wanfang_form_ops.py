"""万方高级检索表单填写 Mixin。"""

import logging
import time
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page

from exceptions import NavigationStateError, ValidationError

logger = logging.getLogger("wanfang_search.interactor")


class WanfangFormMixin:
    """万方高级检索表单操作。"""

    ADVANCED_QUERY_INPUT_SELECTOR = "input.ivu-input.ivu-input-default"
    START_YEAR_DEFAULT_OPTION = "不限"
    END_YEAR_DEFAULT_OPTION = "至今"

    def _open_advanced_search_page(self) -> None:
        """打开万方高级检索页。"""
        self.browser_manager.restore_session(self.config.advanced_search_url)
        self._ensure_captcha_cleared()

        self.page.wait_for_load_state("domcontentloaded")

        if not self._is_advanced_search_page(self.page):
            raise NavigationStateError("打开万方高级检索页面失败")

        self.browser_manager._page = self.page
        self.parser = self._make_parser(self.page)

    def _is_advanced_search_page(self, target_page: Page) -> bool:
        """判断是否为万方高级检索页。"""
        selectors = ["input.ivu-input.ivu-input-default", "span.submit-btn"]
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

    def _fill_advanced_search_form_from_params(self, search_params: Dict[str, Any]) -> None:
        """按共享骨架要求填写高级检索表单。"""
        self._fill_advanced_search_form(
            query=str(search_params["query"]).strip(),
            date_from=search_params.get("date_from"),
            date_to=search_params.get("date_to"),
        )

    def _fill_advanced_search_form(
        self,
        query: str,
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> None:
        """填写万方高级检索表单。

        流程:
        1. 确保存在 2 条检索条件行
        2. 第 1 行: 保持默认检索字段，输入关键词
        3. 第 2 行: 逻辑="或"，保持默认检索字段，输入关键词
        4. 关闭"中英文扩展"
        5. 设置时间范围（ivu-select 下拉）
        6. 点击"检索"按钮
        """
        self._ensure_advanced_condition_rows(2)
        self._set_advanced_condition(0, "主题", query, set_field=False)
        self._set_advanced_condition(1, "关键词", query, logic="或", set_field=False)
        self._disable_chinese_english_expansion()

        self._set_date_year("start", date_from)
        self._set_date_year("end", date_to)

    def _submit_advanced_search(self) -> None:
        """点击检索按钮。"""
        submit_btn = self.page.locator("span.submit-btn").first
        if submit_btn.count() == 0:
            raise ValidationError("未找到检索按钮")

        logger.debug("检索按钮点击前: 按钮可见=%s, 当前URL=%s",
                     submit_btn.is_visible(), self.page.url)
        submit_btn.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        self._dismiss_dialog_if_present()

    def _set_results_per_page(self, count: int) -> None:
        """设置每页显示条数（20/30/50）。"""
        try:
            select_box = self._first_visible_locator([
                "div.wf-select.right-items div.select-box",
                "div.select-box",
            ])
            if select_box.count() == 0:
                return

            current_text = self._safe_text(select_box.locator("span.cur-text").first) or self._safe_text(select_box)
            if str(count) in current_text:
                return

            select_box.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            time.sleep(0.2)

            option = None
            option_selectors = (
                "div.wf-select.right-items div.select-panel div.wf-option",
                "div.select-panel div.wf-option",
                "div.select-box li.ivu-select-item",
            )
            for selector in option_selectors:
                candidate = self.page.locator(selector).filter(has_text=str(count)).first
                if candidate.count() > 0:
                    option = candidate
                    break
            if option is None or option.count() == 0:
                logger.debug("未找到每页 %s 条选项", count)
                return

            option.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            self._wait_for_results_page_size_applied(count)
            logger.info("已设置每页显示 %s 条", count)
        except Exception as exc:
            logger.debug("设置每页条数失败: %s", exc)

    def _ensure_advanced_condition_rows(self, required_count: int) -> None:
        """确保检索条件行数足够。"""
        current_count = self._advanced_query_inputs().count()

        if current_count >= required_count:
            return

        add_btn_selectors = [
            "span.add-condition",
            "button.add-condition",
            "i.ivu-icon-ios-add-circle-outline",
            "span.add-row",
        ]
        deadline = time.time() + self.config.page_timeout
        while current_count < required_count and time.time() < deadline:
            if not self._click_first_available(add_btn_selectors):
                break
            time.sleep(0.3)
            current_count = self._advanced_query_inputs().count()

        if current_count < required_count:
            raise ValidationError("高级检索条件行不足，无法配置双条件检索")

    def _set_advanced_condition(
        self,
        row_index: int,
        field_title: str,
        query: str,
        logic: str = "",
        set_field: bool = True,
    ) -> None:
        """设置单条检索条件。

        万方使用 ivu-select 组件，交互流程为:
        1. 点击 div.ivu-select-selection 展开下拉
        2. 等待 ul.ivu-select-dropdown-list 可见
        3. 点击目标 li.ivu-select-item
        4. 验证 span.ivu-select-selected-value 已变更
        """
        row, query_input = self._get_advanced_condition_row(row_index)

        if logic:
            logic_trigger = self._get_logic_select_trigger(row)
            if logic_trigger.count() > 0:
                self._select_ivu_option(
                    trigger=logic_trigger,
                    option_text=logic,
                    row_index=row_index,
                    label="逻辑运算",
                )

        if set_field:
            field_trigger = self._get_field_select_trigger(row=row, has_logic=bool(logic))
            if field_trigger is not None and field_trigger.count() > 0:
                self._select_ivu_option(
                    trigger=field_trigger,
                    option_text=field_title,
                    row_index=row_index,
                    label="检索字段",
                )
        else:
            logger.debug("跳过检索字段下拉: 行=%s, 使用页面默认值", row_index)

        query_input.fill(query)

    def _select_ivu_option(
        self,
        trigger: Locator,
        option_text: str,
        row_index: int = 0,
        label: str = "",
        strict: bool = False,
    ) -> None:
        """通用 iView 下拉选择交互。"""
        previous_value = self._read_ivu_selected_value(trigger)

        logger.debug("ivu-select操作: 行=%s, 标签=%s, 目标值=%s, 操作前值=%s",
                     row_index, label, option_text, previous_value)

        if previous_value == option_text:
            logger.debug("ivu-select 已是目标值，跳过操作: 行=%s, 标签=%s, 目标值=%s", row_index, label, option_text)
            return

        trigger.click(timeout=self._action_timeout_ms(), no_wait_after=True)
        time.sleep(0.2)

        if not self._click_visible_ivu_option(option_text):
            logger.warning("ivu-select 未找到选项: 行=%s, 标签=%s, 目标值=%s", row_index, label, option_text)
            trigger.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            if strict:
                raise ValidationError(f"未找到下拉选项: {label}={option_text}")
            return

        new_value = self._wait_for_ivu_selected_value(trigger, option_text)
        if new_value != option_text:
            logger.debug(
                "ivu-select 首次点击后值未更新，尝试 JS 重试: 行=%s, 标签=%s, 目标值=%s, 当前值=%s",
                row_index,
                label,
                option_text,
                new_value,
            )
            if self._click_visible_ivu_option(option_text, use_js=True):
                new_value = self._wait_for_ivu_selected_value(trigger, option_text)

        if new_value != option_text:
            message = (
                f"ivu-select 操作后值未更新: 标签={label}, 目标值={option_text}, "
                f"实际值={new_value or previous_value}"
            )
            if strict:
                raise ValidationError(message)
            logger.warning(message)

        logger.debug("ivu-select操作完成: 行=%s, 标签=%s, 操作后值=%s", row_index, label, new_value)

    def _set_date_year(self, boundary: str, year: Optional[str]) -> None:
        """设置年份边界，空值时恢复默认选项。"""
        option_text = self._resolve_date_option_text(boundary, year)
        trigger = self._get_date_select_trigger(boundary)
        if trigger.count() == 0:
            logger.warning("未找到%s年份选择器", "起始" if boundary == "start" else "结束")
            return

        self._select_ivu_option(
            trigger=trigger,
            option_text=option_text,
            label="起始年份" if boundary == "start" else "结束年份",
            strict=True,
        )

    def _resolve_date_option_text(self, boundary: str, year: Optional[str]) -> str:
        """根据边界和值生成目标年份文案。"""
        if year:
            return f"{year}年"
        if boundary == "start":
            return self.START_YEAR_DEFAULT_OPTION
        return self.END_YEAR_DEFAULT_OPTION

    def _get_date_select_trigger(self, boundary: str) -> Locator:
        """返回日期范围对应边界的下拉触发器。"""
        date_container = self.page.locator("span.hrafwidth, div.hrafwidth")
        if date_container.count() == 0:
            return self.page.locator("__missing_date_select__")

        selects = date_container.first.locator("div.ivu-select")
        if boundary == "start":
            target_select = selects.first
        else:
            target_select = selects.nth(1) if selects.count() > 1 else selects.first
        return target_select.locator("div.ivu-select-selection")

    def _collect_date_select_option_texts(self, trigger: Locator) -> list[str]:
        """读取当前展开年份下拉中的所有可见选项文案。"""
        texts: list[str] = []
        try:
            trigger.click(timeout=self._action_timeout_ms(), no_wait_after=True)
            time.sleep(0.2)
        except Exception as exc:
            logger.debug("展开年份下拉失败: %s", exc)
        dropdowns = self.page.locator("div.ivu-select-dropdown")
        for index in range(dropdowns.count()):
            dropdown = dropdowns.nth(index)
            try:
                style = (dropdown.get_attribute("style") or "").replace(" ", "").lower()
            except Exception:
                style = ""
            if "display:none" in style:
                continue
            options = dropdown.locator("li.ivu-select-item")
            for option_index in range(options.count()):
                text = self._safe_text(options.nth(option_index))
                if text:
                    texts.append(text)
        return texts

    def _disable_chinese_english_expansion(self) -> None:
        """关闭"中英文扩展"。"""
        expansion_item = self.page.locator("span.resource-item").filter(has_text="中英文扩展").first
        if expansion_item.count() == 0:
            return

        try:
            before_class = (expansion_item.get_attribute("class") or "").strip()
            logger.debug("中英文扩展关闭前: class=%s", before_class)
            if "active" not in before_class.lower().split():
                return

            expansion_item.evaluate("el => el.click()")
            after_class = self._wait_for_expansion_state(expansion_item, expected_active=False)
            logger.debug("中英文扩展关闭后: class=%s", after_class)
        except Exception as exc:
            logger.debug("关闭中英文扩展失败: %s", exc)

    def _wait_for_expansion_state(self, locator: Locator, expected_active: bool) -> str:
        """等待扩展开关达到目标状态。"""
        deadline = time.time() + float(self.config.action_timeout)
        latest_class = ""
        while time.time() < deadline:
            latest_class = (locator.get_attribute("class") or "").strip()
            is_active = "active" in latest_class.lower().split()
            if is_active == expected_active:
                return latest_class
            time.sleep(0.1)
        return latest_class

    def _safe_text(self, locator: Locator) -> str:
        """安全提取定位器文本。"""
        try:
            if locator.count() == 0:
                return ""
        except Exception:
            return ""
        try:
            return (locator.inner_text() or "").strip()
        except Exception:
            return ""

    def _read_ivu_selected_value(self, trigger: Locator) -> str:
        """读取当前 iView 下拉已选值。"""
        candidate_selectors = (
            "span.ivu-select-selected-value",
            "span.cur-text",
        )
        for selector in candidate_selectors:
            text = self._safe_text(trigger.locator(selector).first)
            if text:
                return text
        return self._safe_text(trigger)

    def _click_visible_ivu_option(self, option_text: str, use_js: bool = False) -> bool:
        """点击当前可见下拉中的目标选项。"""
        dropdown_option_pairs = (
            ("div.ivu-select-dropdown", "li.ivu-select-item"),
            ("div.select-panel", "div.wf-option"),
        )
        for dropdown_selector, option_selector in dropdown_option_pairs:
            dropdowns = self.page.locator(dropdown_selector)
            for index in range(dropdowns.count()):
                dropdown = dropdowns.nth(index)
                try:
                    style = (dropdown.get_attribute("style") or "").replace(" ", "").lower()
                except Exception:
                    style = ""
                if "display:none" in style:
                    continue

                option = dropdown.locator(option_selector).filter(has_text=option_text).first
                if option.count() == 0:
                    continue

                if use_js:
                    option.evaluate(
                        """
                        (element) => {
                            element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                            element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                            element.click();
                        }
                        """
                    )
                else:
                    option.click(timeout=self._action_timeout_ms(), no_wait_after=True)
                return True
        return False

    def _wait_for_ivu_selected_value(self, trigger: Locator, expected_value: str) -> str:
        """等待下拉值更新为目标值。"""
        deadline = time.time() + float(self.config.action_timeout)
        latest_value = self._read_ivu_selected_value(trigger)

        while time.time() < deadline:
            latest_value = self._read_ivu_selected_value(trigger)
            if latest_value == expected_value:
                return latest_value
            time.sleep(0.1)

        return latest_value

    def _advanced_query_inputs(self) -> Locator:
        """返回高级检索条件输入框集合。"""
        return self.page.locator(self.ADVANCED_QUERY_INPUT_SELECTOR)

    def _get_advanced_condition_row(self, row_index: int) -> tuple[Locator, Locator]:
        """按输入框位置推导对应的条件行容器。"""
        query_inputs = self._advanced_query_inputs()
        if query_inputs.count() <= row_index:
            raise ValidationError(f"未找到第 {row_index + 1} 条检索词输入框")

        query_input = query_inputs.nth(row_index)
        row = query_input.locator("xpath=ancestor::div[contains(@class,'input-item')][1]")
        if row.count() == 0:
            raise ValidationError(f"未找到第 {row_index + 1} 条高级检索条件")
        return row, query_input

    def _get_logic_select_trigger(self, row: Locator) -> Locator:
        """返回逻辑运算下拉触发器。"""
        return row.locator("div.option-area div.search-option div.ivu-select-selection").first

    def _get_field_select_trigger(self, row: Locator, has_logic: bool) -> Optional[Locator]:
        """返回检索字段下拉触发器。"""
        del has_logic
        field_trigger = row.locator("div.input-area div.search-option.field div.ivu-select-selection").first
        if field_trigger.count() > 0:
            return field_trigger
        return None
