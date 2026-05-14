"""万方页面交互测试。"""

import json
import tempfile
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.script_loader import load_script_module

SCRIPT_DIR = ROOT_DIR / "wanfang-search" / "scripts"
_exceptions_module = load_script_module(SCRIPT_DIR, "exceptions", "wanfang_exceptions_module")
_interactor_module = load_script_module(
    SCRIPT_DIR,
    "wanfang_search_interactor",
    "wanfang_search_interactor_module",
)
_cli_module = load_script_module(SCRIPT_DIR, "cli", "wanfang_cli_runtime_module")
_utils_module = load_script_module(SCRIPT_DIR, "utils", "wanfang_utils_module")
_result_parser_module = load_script_module(SCRIPT_DIR, "result_parser", "wanfang_result_parser_module")

WanfangSearchInteractor = _interactor_module.WanfangSearchInteractor
ValidationError = WanfangSearchInteractor._prepare_progress_store.__globals__["ValidationError"]
TimeoutError = WanfangSearchInteractor._wait_for_any_selector.__globals__["TimeoutError"]
NavigationStateError = WanfangSearchInteractor._open_advanced_search_page.__globals__["NavigationStateError"]
ResultParser = _result_parser_module.ResultParser
print_human_readable = _utils_module.print_human_readable


class FakeLocator:
    """模拟页面定位器。"""

    def __init__(
        self,
        text: str = "",
        count_value: int = 1,
        class_name: str = "",
        attributes: dict[str, str] | None = None,
        children: dict[str, object] | None = None,
        on_click=None,
        checked: bool = False,
    ) -> None:
        self.text = text
        self._count_value = count_value
        self.class_name = class_name
        self.attributes = attributes or {}
        self.children = children or {}
        self.on_click = on_click
        self.checked = checked
        self.click_calls: list[dict[str, object]] = []
        self.scroll_calls = 0
        self.evaluate_calls: list[str] = []

    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        return self._count_value

    def nth(self, index: int) -> "FakeLocator":
        del index
        return self

    def locator(self, selector: str):
        return self.children.get(selector, FakeLocator(count_value=0))

    def filter(self, has_text=None, has=None, has_not=None):
        del has_text, has, has_not
        return self

    def click(self, **kwargs) -> None:
        self.click_calls.append(kwargs)
        if self.on_click is not None:
            self.on_click(self)

    def check(self, force: bool = False, timeout: int = 0) -> None:
        del force, timeout
        self.checked = True
        if self.on_click is not None:
            self.on_click(self)

    def is_checked(self) -> bool:
        return self.checked

    def is_visible(self) -> bool:
        return self._count_value > 0

    def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        del timeout
        self.scroll_calls += 1

    def evaluate(self, script: str) -> None:
        self.evaluate_calls.append(script)
        if self.on_click is not None:
            self.on_click(self)

    def inner_text(self) -> str:
        return self.text

    def get_attribute(self, name: str) -> str:
        if name == "class":
            return self.class_name
        return self.attributes.get(name, "")

    def fill(self, value: str) -> None:
        self.text = value

    def wait_for(self, state: str = "visible", timeout: int = 0) -> None:
        del state, timeout


class FakeLocatorGroup:
    """模拟定位器集合。"""

    def __init__(self, items: list[FakeLocator] | None = None) -> None:
        self.items = items or []

    @property
    def first(self) -> FakeLocator:
        if self.items:
            return self.items[0]
        return FakeLocator(count_value=0)

    def count(self) -> int:
        return len(self.items)

    def nth(self, index: int) -> FakeLocator:
        return self.items[index]

    def filter(self, has_text=None, has=None, has_not=None):
        del has_text, has, has_not
        return self


class FakePopupContext:
    """模拟 expect_popup 上下文。"""

    def __init__(self, popup_page=None, error: Exception | None = None) -> None:
        self._popup_page = popup_page
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    @property
    def value(self):
        if self._error is not None:
            raise self._error
        return self._popup_page


class FakeElement:
    """模拟 query_selector 返回元素。"""

    def __init__(self, text: str = "", value: str | None = None, attributes: dict[str, str] | None = None) -> None:
        self.text = text
        self.value = value
        self.attributes = attributes or {}

    def inner_text(self) -> str:
        return self.text

    def get_attribute(self, name: str) -> str:
        return self.attributes.get(name, "")

    def evaluate(self, script: str):
        del script
        return self.value


class FakePage:
    """模拟页面对象。"""

    def __init__(
        self,
        mapping: dict[str, object],
        url: str = "https://example.com/results",
        popup_context: FakePopupContext | None = None,
    ) -> None:
        self.mapping = mapping
        self.url = url
        self.context = SimpleNamespace(pages=[self])
        self.popup_context = popup_context or FakePopupContext(error=RuntimeError("no popup"))
        self.load_state_calls: list[tuple[str, int]] = []
        self.closed = False

    def locator(self, selector: str):
        return self.mapping.get(selector, FakeLocator(count_value=0))

    def query_selector(self, selector: str):
        return self.mapping.get(selector)

    def query_selector_all(self, selector: str):
        return self.mapping.get(selector, [])

    def expect_popup(self, timeout: int = 0) -> FakePopupContext:
        del timeout
        return self.popup_context

    def wait_for_load_state(self, state: str = "domcontentloaded", timeout: int = 0) -> None:
        self.load_state_calls.append((state, timeout))

    def close(self) -> None:
        self.closed = True


class WanfangInteractorSelectionTestCase(unittest.TestCase):
    """验证勾选逻辑。"""

    def setUp(self) -> None:
        config = SimpleNamespace(page_timeout=1, action_timeout=1, page_change_timeout=1)
        page = FakePage({})
        browser_manager = SimpleNamespace(is_captcha_visible=lambda _page: False)
        self.interactor = WanfangSearchInteractor(page=page, config=config, browser_manager=browser_manager)
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"page": "1/10", "current_page": 1})

    def test_select_batch_results_rejects_page_shortfall_in_full_export_mode(self) -> None:
        """全量导出模式下出现页面缺口应直接失败，避免游标漂移。"""
        row_locator = FakeLocatorGroup([FakeLocator() for _ in range(50)])
        self.interactor.page = FakePage({"div.normal-list": row_locator})

        with (
            patch.object(self.interactor, "_wait_for_results_ready"),
            patch.object(self.interactor, "_select_rows_on_current_page", return_value=47),
            patch.object(self.interactor, "_current_results_page_number", return_value=1),
            self.assertRaises(ValidationError),
        ):
            self.interactor._select_batch_results(export_limit=100, row_offset=0, strict_target=False)

    def test_select_batch_results_keeps_topping_up_in_strict_mode(self) -> None:
        """限定数量模式下应继续向后补足目标数量。"""
        row_locator = FakeLocatorGroup([FakeLocator() for _ in range(50)])
        self.interactor.page = FakePage({"div.normal-list": row_locator})

        with (
            patch.object(self.interactor, "_wait_for_results_ready"),
            patch.object(self.interactor, "_select_rows_on_current_page", side_effect=[50, 3]),
            patch.object(self.interactor, "_current_results_page_number", side_effect=[1, 2]),
            patch.object(self.interactor, "_goto_next_results_page", return_value=True) as goto_next_results_page,
        ):
            result = self.interactor._select_batch_results(export_limit=53, row_offset=0, strict_target=True)

        self.assertEqual(result["selected_count"], 53)
        self.assertEqual(result["next_row_offset"], 3)
        self.assertEqual(result["page_row_count"], 50)
        self.assertEqual(result["start_page"], 1)
        self.assertEqual(result["end_page"], 2)
        self.assertEqual(goto_next_results_page.call_count, 1)

    def test_select_batch_results_returns_reached_end_when_last_page_exhausted(self) -> None:
        """续跑游标已在末页末尾时应按正常结束返回。"""
        row_locator = FakeLocatorGroup([FakeLocator() for _ in range(50)])
        self.interactor.page = FakePage({"div.normal-list": row_locator})

        with (
            patch.object(self.interactor, "_wait_for_results_ready"),
            patch.object(self.interactor, "_goto_next_results_page", return_value=False) as goto_next_results_page,
        ):
            result = self.interactor._select_batch_results(export_limit=100, row_offset=50, strict_target=True)

        self.assertEqual(result["selected_count"], 0)
        self.assertEqual(result["next_row_offset"], 50)
        self.assertEqual(result["page_row_count"], 50)
        self.assertTrue(result["reached_end"])
        goto_next_results_page.assert_called_once()

    def test_select_rows_on_current_page_uses_row_state_when_selected_count_unavailable(self) -> None:
        """页级全选校验应允许退回页内真实勾选状态。"""
        row_inputs = [FakeLocator(checked=False), FakeLocator(checked=False)]
        row_checkboxes = [
            FakeLocator(
                children={
                    "input.ivu-checkbox-input": row_inputs[0],
                    "span.ivu-checkbox": FakeLocator(class_name="ivu-checkbox"),
                }
            ),
            FakeLocator(
                children={
                    "input.ivu-checkbox-input": row_inputs[1],
                    "span.ivu-checkbox": FakeLocator(class_name="ivu-checkbox"),
                }
            ),
        ]
        checkbox_group = FakeLocatorGroup(row_checkboxes)
        select_all_input = FakeLocator(checked=False)

        def mark_all_checked(_locator: FakeLocator) -> None:
            select_all_input.checked = True
            for input_locator in row_inputs:
                input_locator.checked = True

        select_all_checkbox = FakeLocator(
            children={
                "input.ivu-checkbox-input": select_all_input,
                "span.ivu-checkbox": FakeLocator(class_name="ivu-checkbox"),
                "span.ivu-checkbox-inner": FakeLocator(on_click=mark_all_checked),
            },
            on_click=mark_all_checked,
        )
        select_all_container = FakeLocator(
            children={
                "label.ivu-checkbox-wrapper": select_all_checkbox,
            }
        )
        self.interactor.page = FakePage(
            {
                "div.normal-list div.wf-checkbox label.ivu-checkbox-wrapper": checkbox_group,
                "div.top-check-bar div.wf-checkbox": select_all_container,
                "div.top-check-bar div.wf-checkbox label.ivu-checkbox-wrapper": select_all_checkbox,
            }
        )

        with patch("wanfang_selection_ops.time.sleep", lambda _seconds: None):
            selected_count = self.interactor._select_rows_on_current_page(
                row_offset=0,
                page_target_count=2,
                row_count=2,
            )

        self.assertEqual(selected_count, 2)
        self.assertTrue(all(input_locator.checked for input_locator in row_inputs))

    def test_select_rows_on_current_page_should_fail_immediately_when_full_page_select_all_cannot_be_verified(self) -> None:
        """整页全选无法确认成功时不应回退逐条勾选。"""
        checkbox_group = FakeLocatorGroup([FakeLocator(), FakeLocator()])
        self.interactor.page = FakePage(
            {
                "div.normal-list div.wf-checkbox label.ivu-checkbox-wrapper": checkbox_group,
                "div.top-check-bar div.wf-checkbox label.ivu-checkbox-wrapper": FakeLocator(),
            }
        )

        with (
            patch.object(self.interactor, "_try_select_all_on_current_page", return_value=False),
            patch.object(self.interactor, "_ensure_checkbox_checked") as ensure_checkbox_checked,
            self.assertRaises(ValidationError),
        ):
            self.interactor._select_rows_on_current_page(
                row_offset=0,
                page_target_count=2,
                row_count=2,
            )

        ensure_checkbox_checked.assert_not_called()

    def test_ensure_select_all_checked_should_fall_back_to_checkbox_span(self) -> None:
        """全选 label 点击不生效时应继续尝试更精确的 checkbox 节点。"""
        select_all_input = FakeLocator(checked=False)

        def mark_checked(_locator: FakeLocator) -> None:
            select_all_input.checked = True

        checkbox_span = FakeLocator(on_click=mark_checked)
        select_all = FakeLocator(
            children={
                "input.ivu-checkbox-input": select_all_input,
                "span.ivu-checkbox": checkbox_span,
                "span.ivu-checkbox-inner": FakeLocator(),
            }
        )
        select_all_container = FakeLocator(
            children={
                "label.ivu-checkbox-wrapper": select_all,
            }
        )

        with (
            patch.object(self.interactor, "_ensure_checkbox_checked"),
            patch("wanfang_selection_ops.time.sleep", lambda _seconds: None),
        ):
            self.interactor._ensure_select_all_checked(
                select_all_container=select_all_container,
                select_all=select_all,
                prefer_precise_targets=False,
            )

        self.assertTrue(select_all_input.checked)

    def test_is_checkbox_checked_prefers_input_state_over_wrapper_class(self) -> None:
        """勾选状态判定应优先信任原生 input。"""
        checkbox = FakeLocator(
            children={
                "input.ivu-checkbox-input": FakeLocator(checked=False),
                "span.ivu-checkbox": FakeLocator(class_name="ivu-checkbox ivu-checkbox-checked"),
            }
        )

        self.assertFalse(self.interactor._is_checkbox_checked(checkbox))

    def test_select_batch_for_export_rebuilds_search_context_before_retry(self) -> None:
        """当前批次首次勾选失败后应先重建检索上下文再重试。"""
        success_result = {
            "selected_count": 20,
            "next_row_offset": 20,
            "page_row_count": 20,
            "start_page": 3,
            "end_page": 3,
        }

        with (
            patch.object(self.interactor, "_clear_selected_results"),
            patch.object(
                self.interactor,
                "_select_batch_results",
                side_effect=[ValidationError("当前页勾选未达标"), success_result],
            ),
            patch.object(self.interactor, "_rebuild_search_results_context_for_batch") as rebuild_context,
            patch.object(self.interactor, "_restart_browser_for_batch_retry") as restart_browser,
        ):
            result = self.interactor._select_batch_for_export(
                search_params={"query": "新青年", "date_from": None, "date_to": None, "max_download": 20},
                batch_index=2,
                batch_target=20,
                current_page=3,
                current_row_offset=0,
                strict_target=True,
            )

        self.assertEqual(result["selected_count"], 20)
        rebuild_context.assert_called_once_with(
            search_params={"query": "新青年", "date_from": None, "date_to": None, "max_download": 20},
            batch_index=2,
            target_page=3,
            current_row_offset=0,
        )
        restart_browser.assert_not_called()

    def test_select_batch_for_export_should_restart_browser_after_local_retries_exhausted(self) -> None:
        """当前批次连续失败后应升级到浏览器级重试。"""
        success_result = {
            "selected_count": 20,
            "next_row_offset": 20,
            "page_row_count": 20,
            "start_page": 3,
            "end_page": 3,
        }

        with (
            patch.object(self.interactor, "_clear_selected_results"),
            patch.object(
                self.interactor,
                "_select_batch_results",
                side_effect=[ValidationError("首次失败"), ValidationError("再次失败"), success_result],
            ),
            patch.object(self.interactor, "_rebuild_search_results_context_for_batch") as rebuild_context,
            patch.object(self.interactor, "_restart_browser_for_batch_retry") as restart_browser,
        ):
            result = self.interactor._select_batch_for_export(
                search_params={"query": "新青年", "date_from": None, "date_to": None, "max_download": 20},
                batch_index=2,
                batch_target=20,
                current_page=3,
                current_row_offset=0,
                strict_target=True,
            )

        self.assertEqual(result["selected_count"], 20)
        rebuild_context.assert_called_once()
        restart_browser.assert_called_once_with(
            search_params={"query": "新青年", "date_from": None, "date_to": None, "max_download": 20},
            batch_index=2,
            target_page=3,
            current_row_offset=0,
        )


class WanfangInteractorFormTestCase(unittest.TestCase):
    """验证高级检索表单定位逻辑。"""

    def setUp(self) -> None:
        config = SimpleNamespace(page_timeout=1, action_timeout=1, page_change_timeout=1)
        page = FakePage({})
        browser_manager = SimpleNamespace(is_captcha_visible=lambda _page: False)
        self.interactor = WanfangSearchInteractor(page=page, config=config, browser_manager=browser_manager)

    def test_ensure_advanced_condition_rows_uses_visible_query_inputs(self) -> None:
        """条件行数量判断应基于真实输入框数量。"""
        input_group = FakeLocatorGroup([FakeLocator(), FakeLocator()])
        self.interactor.page = FakePage({self.interactor.ADVANCED_QUERY_INPUT_SELECTOR: input_group})

        self.interactor._ensure_advanced_condition_rows(2)

    def test_get_field_select_trigger_skips_logic_select_when_present(self) -> None:
        """存在逻辑下拉时，字段下拉应取第二个触发器。"""
        second_trigger = FakeLocator(text="题名或关键词")
        row = FakeLocator(
            children={
                "div.input-area div.search-option.field div.ivu-select-selection": second_trigger,
            }
        )

        trigger = self.interactor._get_field_select_trigger(row=row, has_logic=True)

        self.assertIs(trigger, second_trigger)

    def test_get_logic_select_trigger_uses_option_area_selector(self) -> None:
        """逻辑运算下拉应取左侧 option-area 中的选择器。"""
        logic_trigger = FakeLocator(text="与")
        row = FakeLocator(
            children={
                "div.option-area div.search-option div.ivu-select-selection": logic_trigger,
            }
        )

        trigger = self.interactor._get_logic_select_trigger(row)

        self.assertIs(trigger, logic_trigger)

    def test_fill_advanced_search_form_skips_field_dropdown_changes(self) -> None:
        """万方高级检索主流程不应主动切换检索字段下拉。"""
        with (
            patch.object(self.interactor, "_ensure_advanced_condition_rows"),
            patch.object(self.interactor, "_set_advanced_condition") as set_advanced_condition,
            patch.object(self.interactor, "_disable_chinese_english_expansion"),
            patch.object(self.interactor, "_set_date_year") as set_date_year,
        ):
            self.interactor._fill_advanced_search_form(
                query="新青年",
                date_from=None,
                date_to=None,
            )

        self.assertEqual(
            set_advanced_condition.call_args_list,
            [
                call(0, "主题", "新青年", set_field=False),
                call(1, "关键词", "新青年", logic="或", set_field=False),
            ],
        )
        self.assertEqual(
            set_date_year.call_args_list,
            [
                call("start", None),
                call("end", None),
            ],
        )

    def test_fill_advanced_search_form_clears_start_year_when_initial_date_from_missing(self) -> None:
        """复用页面时未传起始年应显式恢复起始年份默认值。"""
        with (
            patch.object(self.interactor, "_ensure_advanced_condition_rows"),
            patch.object(self.interactor, "_set_advanced_condition"),
            patch.object(self.interactor, "_disable_chinese_english_expansion"),
            patch.object(self.interactor, "_set_date_year") as set_date_year,
        ):
            self.interactor._fill_advanced_search_form(
                query="新青年",
                date_from=None,
                date_to="1978",
            )

        self.assertEqual(
            set_date_year.call_args_list,
            [
                call("start", None),
                call("end", "1978"),
            ],
        )

    def test_set_date_year_uses_default_options_for_empty_boundaries(self) -> None:
        """空年份边界应写入页面默认项，避免沿用上一轮残留值。"""
        trigger = FakeLocator()

        with (
            patch.object(self.interactor, "_get_date_select_trigger", return_value=trigger),
            patch.object(self.interactor, "_select_ivu_option") as select_ivu_option,
        ):
            self.interactor._set_date_year("start", None)
            self.interactor._set_date_year("end", None)

        self.assertEqual(
            select_ivu_option.call_args_list,
            [
                call(trigger=trigger, option_text="不限", label="起始年份", strict=True),
                call(trigger=trigger, option_text="至今", label="结束年份", strict=True),
            ],
        )

    def test_submit_advanced_search_uses_action_timeout_without_navigation_wait(self) -> None:
        """点击检索按钮应使用动作超时并关闭默认导航等待。"""
        submit_btn = FakeLocator()
        self.interactor.page = FakePage({"span.submit-btn": submit_btn}, url="https://example.com/advanced-search")

        with patch.object(self.interactor, "_dismiss_dialog_if_present") as dismiss_dialog:
            self.interactor._submit_advanced_search()

        self.assertEqual(
            submit_btn.click_calls,
            [{"timeout": 1000, "no_wait_after": True}],
        )
        dismiss_dialog.assert_called_once()

    def test_open_advanced_search_page_uses_current_tab_advanced_search_url(self) -> None:
        """万方高级检索应直接在当前页打开目标 URL，而不是点击首页入口新开页签。"""
        restore_calls: list[str] = []
        self.interactor.browser_manager = SimpleNamespace(
            restore_session=lambda target_url: restore_calls.append(target_url),
            is_captcha_visible=lambda _page: False,
            _page=None,
        )
        self.interactor.config.advanced_search_url = "https://example.com/advanced-search"
        self.interactor.page = FakePage(
            {
                "input.ivu-input.ivu-input-default": FakeLocator(),
                "span.submit-btn": FakeLocator(),
            },
            url="https://example.com/advanced-search",
        )

        self.interactor._open_advanced_search_page()

        self.assertEqual(restore_calls, ["https://example.com/advanced-search"])
        self.assertIs(self.interactor.browser_manager._page, self.interactor.page)

    def test_open_advanced_search_page_raises_navigation_state_error_when_target_not_ready(self) -> None:
        """高级检索页未就绪时应抛出可自动续跑的导航状态异常。"""
        self.interactor.browser_manager = SimpleNamespace(
            restore_session=lambda target_url: None,
            is_captcha_visible=lambda _page: False,
            _page=None,
        )
        self.interactor.config.advanced_search_url = "https://example.com/advanced-search"
        self.interactor.page = FakePage({}, url="https://example.com/advanced-search")

        with self.assertRaises(NavigationStateError):
            self.interactor._open_advanced_search_page()

    def test_select_ivu_option_uses_action_timeout_without_navigation_wait(self) -> None:
        """iView 下拉点击应使用动作超时并关闭默认导航等待。"""
        trigger = FakeLocator(
            children={"span.ivu-select-selected-value": FakeLocator(text="与")}
        )
        option = FakeLocator()
        dropdown = FakeLocator(children={"li.ivu-select-item": option})
        self.interactor.page = FakePage({"div.ivu-select-dropdown": dropdown})

        self.interactor._select_ivu_option(
            trigger=trigger,
            option_text="或",
            row_index=1,
            label="逻辑运算",
        )

        self.assertEqual(
            trigger.click_calls,
            [{"timeout": 1000, "no_wait_after": True}],
        )
        self.assertEqual(
            option.click_calls,
            [{"timeout": 1000, "no_wait_after": True}],
        )

    def test_disable_chinese_english_expansion_uses_stable_locator_after_click(self) -> None:
        """中英文扩展关闭后应继续读取同一文本节点，而非依赖 active 选择器。"""
        expansion_item = FakeLocator(
            class_name="resource-item active",
            on_click=lambda locator: setattr(locator, "class_name", "resource-item"),
        )
        self.interactor.page = FakePage({"span.resource-item": expansion_item})

        with patch("wanfang_form_ops.time.sleep", lambda _seconds: None):
            self.interactor._disable_chinese_english_expansion()

        self.assertEqual(expansion_item.evaluate_calls, ["el => el.click()"])
        self.assertEqual(expansion_item.class_name, "resource-item")

    def test_select_ivu_option_strict_should_raise_when_value_not_changed(self) -> None:
        """严格模式下，选项点击后值未更新应直接报错。"""
        trigger = FakeLocator(
            children={"span.ivu-select-selected-value": FakeLocator(text="至今")}
        )

        with (
            patch.object(self.interactor, "_click_visible_ivu_option", return_value=True),
            patch.object(self.interactor, "_wait_for_ivu_selected_value", return_value="至今"),
        ):
            with self.assertRaises(ValidationError):
                self.interactor._select_ivu_option(
                    trigger=trigger,
                    option_text="2025年",
                    label="结束年份",
                    strict=True,
                )

    def test_read_ivu_selected_value_supports_cur_text(self) -> None:
        """读取下拉当前值时应兼容 cur-text 结构。"""
        trigger = FakeLocator(children={"span.cur-text": FakeLocator(text="至今")})

        value = self.interactor._read_ivu_selected_value(trigger)

        self.assertEqual(value, "至今")

    def test_set_results_per_page_supports_wf_option_dropdown(self) -> None:
        """每页条数下拉应兼容 wf-option 新结构。"""
        select_box = FakeLocator(children={"span.cur-text": FakeLocator(text="显示 20 条")})
        option = FakeLocator(text="显示 50 条")
        self.interactor.page = FakePage(
            {
                "div.wf-select.right-items div.select-box": select_box,
                "div.wf-select.right-items div.select-panel div.wf-option": FakeLocatorGroup([option]),
            }
        )

        with patch.object(self.interactor, "_wait_for_results_page_size_applied") as wait_applied:
            self.interactor._set_results_per_page(50)

        self.assertEqual(
            select_box.click_calls,
            [{"timeout": 1000, "no_wait_after": True}],
        )
        self.assertEqual(
            option.click_calls,
            [{"timeout": 1000, "no_wait_after": True}],
        )
        wait_applied.assert_called_once_with(50)

    def test_wait_for_results_ready_requires_total_number_marker(self) -> None:
        """结果页等待不应被列表中间态节点提前放行。"""
        self.interactor.config.page_timeout = 0.01
        self.interactor.page = FakePage({"div.normal-list": FakeLocator(count_value=3)})

        with patch("wanfang_page_ops.time.sleep", lambda _seconds: None):
            with self.assertRaises(TimeoutError):
                self.interactor._wait_for_results_ready()

    def test_wait_for_results_page_size_applied_supports_wf_select_text(self) -> None:
        """等待每页条数生效时应兼容 wf-select 文本。"""
        self.interactor.page = FakePage(
            {"div.wf-select.right-items span.cur-text": FakeLocator(text="显示 50 条")}
        )

        with patch("wanfang_page_ops.time.sleep", lambda _seconds: None):
            self.interactor._wait_for_results_page_size_applied(50)

    def test_wait_for_results_ready_accepts_no_results_tip(self) -> None:
        """无结果提示出现时也应视为结果页已完成加载。"""
        self.interactor.page = FakePage({"div.tip-content": FakeLocator()})

        with patch("wanfang_page_ops.time.sleep", lambda _seconds: None):
            self.interactor._wait_for_results_ready()


class WanfangInteractorExportTestCase(unittest.TestCase):
    """验证导出页交互逻辑。"""

    def setUp(self) -> None:
        config = SimpleNamespace(page_timeout=1, action_timeout=1, page_change_timeout=1)
        page = FakePage({})
        browser_manager = SimpleNamespace(is_captcha_visible=lambda _page: False)
        self.interactor = WanfangSearchInteractor(page=page, config=config, browser_manager=browser_manager)

    def test_open_export_page_supports_in_place_rendered_page(self) -> None:
        """批量引用在当前标签打开时也应识别成功。"""
        export_button = FakeLocator()
        export_marker = FakeLocator()
        page = FakePage(
            {
                "span.export-btn": export_button,
                "div.export-text-action": export_marker,
            },
            popup_context=FakePopupContext(error=RuntimeError("no popup")),
        )
        self.interactor.page = page

        export_page = self.interactor._open_export_page()

        self.assertIs(export_page, page)
        self.assertEqual(
            export_button.click_calls,
            [{"timeout": 1000, "no_wait_after": True}],
        )

    def test_switch_export_tab_accepts_ready_marker_without_active_class(self) -> None:
        """导出标签切换成功判定不应依赖 active class。"""
        tab = FakeLocator(class_name="export-tabs_item")
        export_page = FakePage(
            {
                "div#tab-m5": tab,
                "div.export-text-action": FakeLocator(text="导出XLS"),
            }
        )

        with patch("wanfang_export_ops.time.sleep", lambda _seconds: None):
            self.interactor._switch_export_tab(export_page, "div#tab-m5", "自定义格式")

        self.assertEqual(tab.click_calls, [{"timeout": 1000, "no_wait_after": True}])

    def test_switch_export_tab_checks_target_tab_state(self) -> None:
        """导出标签切换应检查目标 tab 状态，而不是固定读取第一个 tab。"""
        target_tab = FakeLocator(class_name="export-tabs_item is-active")
        export_page = FakePage(
            {
                "div#tab-m2": target_tab,
                "div.export-text-action": FakeLocator(text="导出TXT"),
            }
        )

        self.interactor._switch_export_tab(export_page, "div#tab-m2", "参考文献")

        self.assertEqual(target_tab.click_calls, [])


class WanfangInteractorNavigationTestCase(unittest.TestCase):
    """验证续跑恢复页码逻辑。"""

    def setUp(self) -> None:
        config = SimpleNamespace(page_timeout=1, action_timeout=1, page_change_timeout=1)
        page = FakePage({})
        browser_manager = SimpleNamespace(is_captcha_visible=lambda _page: False)
        self.interactor = WanfangSearchInteractor(page=page, config=config, browser_manager=browser_manager)

    def test_find_resume_target_page_link_prefers_farthest_visible_page(self) -> None:
        """恢复页码时应优先选择当前可见范围内最大的数字页。"""
        page_12 = FakeLocator(text="12")
        current_page = FakeLocator(text="13", class_name="pager active")
        page_15 = FakeLocator(text="15")
        page_17 = FakeLocator(text="17")
        next_link = FakeLocator(text="下一页")
        self.interactor.page = FakePage(
            {
                "span.pager": FakeLocatorGroup([page_12, current_page, page_15, page_17, next_link]),
            }
        )

        target_link = self.interactor._find_resume_target_page_link(current_page=13, target_page=30)

        self.assertIs(target_link, page_17)

    def test_restore_results_position_prefers_visible_page_jump_before_next(self) -> None:
        """恢复执行时应先尝试点击可见数字页。"""
        jump_link = FakeLocator(text="17")
        pages = iter(
            [
                {"current_page": 13, "page": "13/40"},
                {"current_page": 17, "page": "17/40"},
                {"current_page": 18, "page": "18/40"},
            ]
        )
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: next(pages))

        with (
            patch.object(
                self.interactor,
                "_find_resume_target_page_link",
                side_effect=[jump_link, None],
            ) as find_resume_target_page_link,
            patch.object(self.interactor, "_goto_results_page_by_link", return_value=True) as goto_results_page_by_link,
            patch.object(self.interactor, "_goto_next_results_page", return_value=True) as goto_next_results_page,
        ):
            self.interactor._restore_results_position(target_page=18)

        self.assertEqual(
            find_resume_target_page_link.call_args_list,
            [
                call(current_page=13, target_page=18),
                call(current_page=17, target_page=18),
            ],
        )
        goto_results_page_by_link.assert_called_once_with(jump_link)
        goto_next_results_page.assert_called_once()


class WanfangInteractorProgressTestCase(unittest.TestCase):
    """验证断点续跑相关逻辑。"""

    def setUp(self) -> None:
        config = SimpleNamespace(page_timeout=1)
        page = SimpleNamespace(url="https://example.com/results")
        self.interactor = WanfangSearchInteractor(page=page, config=config, browser_manager=None)
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 2, "page": "2/10"})

    def test_build_resume_runtime_resets_when_history_files_missing(self) -> None:
        """历史批次文件缺失时应自动重置计数，从头开始。"""
        with TemporaryDirectory() as temp_dir:
            missing_file = Path(temp_dir) / "missing.xlsx"
            resume_data = {
                "status": "failed",
                "runtime": {
                    "exported_total": 10,
                    "exported_batches": 1,
                    "next_batch_index": 2,
                    "current_page": 3,
                    "current_row_offset": 20,
                    "enriched_batch_files": [str(missing_file)],
                },
            }

            runtime = self.interactor._build_resume_runtime(
                resume_data=resume_data,
                output_dir=Path(temp_dir),
                planned_download=100,
                batch_count=2,
                total=200,
            )
            self.assertEqual(runtime["exported_batches"], 0)
            self.assertEqual(runtime["exported_total"], 0)
            self.assertEqual(runtime["next_batch_index"], 1)
            self.assertEqual(runtime["current_page"], 1)
            self.assertEqual(runtime["current_row_offset"], 0)
            self.assertEqual(runtime["enriched_batch_files"], [])

    def test_save_progress_snapshot_persists_runtime_context(self) -> None:
        """进度快照应落盘关键运行态。"""
        with TemporaryDirectory() as temp_dir:
            progress_path = Path(temp_dir) / "progress.json"
            batch_file = Path(temp_dir) / "batch.xlsx"
            batch_file.write_text("ok", encoding="utf-8")
            output_dir = Path(temp_dir) / "outputs"

            from progress_store import SearchProgressStore

            store = SearchProgressStore(progress_path)
            self.interactor._save_progress_snapshot(
                progress_store=store,
                status="running",
                query="新青年",
                date_from="2020",
                date_to="2025",
                max_download=100,
                output_dir=output_dir,
                planned_download=100,
                batch_count=1,
                exported_total=50,
                exported_batches=1,
                next_batch_index=2,
                current_page=2,
                current_row_offset=10,
                enriched_batch_files=[batch_file],
                final_file_path="",
                error=RuntimeError("翻页失败"),
            )

            state = json.loads(progress_path.read_text(encoding="utf-8"))

        self.assertEqual(state["status"], "running")
        self.assertEqual(state["search_params"]["query"], "新青年")
        self.assertEqual(state["runtime"]["current_page"], 2)
        self.assertEqual(state["runtime"]["current_row_offset"], 10)
        self.assertEqual(state["runtime"]["enriched_batch_files"], [str(batch_file.resolve())])
        self.assertEqual(state["last_error"]["type"], "RuntimeError")

    def test_save_progress_snapshot_uses_resume_cursor_page_text(self) -> None:
        """失败留痕时应写入恢复游标页，而不是现场浏览页。"""
        with TemporaryDirectory() as temp_dir:
            progress_path = Path(temp_dir) / "progress.json"
            batch_file = Path(temp_dir) / "batch.xlsx"
            batch_file.write_text("ok", encoding="utf-8")
            output_dir = Path(temp_dir) / "outputs"
            self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 30, "page": "30/153"})

            from progress_store import SearchProgressStore

            store = SearchProgressStore(progress_path)
            self.interactor._save_progress_snapshot(
                progress_store=store,
                status="failed",
                query="新青年",
                date_from=None,
                date_to="2025",
                max_download=None,
                output_dir=output_dir,
                planned_download=7625,
                batch_count=16,
                exported_total=1000,
                exported_batches=2,
                next_batch_index=3,
                current_page=21,
                current_row_offset=0,
                enriched_batch_files=[batch_file],
                final_file_path="",
                error=RuntimeError("导出失败"),
            )

            state = json.loads(progress_path.read_text(encoding="utf-8"))

        self.assertEqual(state["runtime"]["current_page"], 21)
        self.assertEqual(state["runtime"]["page_text"], "21/153")

    def test_safe_progress_page_context_uses_cached_results_context(self) -> None:
        """非结果页场景应优先复用已缓存上下文。"""
        def raise_not_results():
            raise RuntimeError("当前页面不是结果页")

        cached_context = {
            "current_page": 3,
            "page_text": "3/10",
            "url": "https://example.com/results?page=3",
        }
        self.interactor._last_results_page_context = cached_context
        self.interactor.page = SimpleNamespace(url="https://example.com/export")
        self.interactor.parser = SimpleNamespace(parse_results_summary=raise_not_results)

        page_context = self.interactor._safe_progress_page_context()

        self.assertEqual(page_context, cached_context)

    def test_prepare_next_batch_cursor_advances_to_next_page_after_full_page(self) -> None:
        """整页完成后应把恢复游标推进到下一页起点。"""
        with patch.object(self.interactor, "_goto_next_results_page", return_value=True):
            self.interactor._current_results_page_number = lambda: 36
            cursor = self.interactor._prepare_next_batch_cursor(
                {
                    "next_row_offset": 50,
                    "page_row_count": 50,
                    "end_page": 35,
                }
            )

        self.assertEqual(
            cursor,
            {
                "current_page": 36,
                "current_row_offset": 0,
            },
        )


class WanfangResultParserTestCase(unittest.TestCase):
    """验证结果页解析逻辑。"""

    def test_parse_results_summary_supports_total_and_page_number(self) -> None:
        """应解析总数与页码。"""
        page = FakePage(
            {
                "span.total-number": FakeElement("找到 7618 条文献"),
                "span.total-number span.mark-number": FakeElement("7,618"),
                "span.currentpage": FakeElement(" 1 /"),
                "span.page-number": FakeElement(" 1 / 381"),
            },
            url="https://example.com/results",
        )

        summary = ResultParser(page).parse_results_summary()

        self.assertEqual(summary["total"], 7618)
        self.assertEqual(summary["page"], "1/381")
        self.assertEqual(summary["current_page"], 1)
        self.assertEqual(summary["total_pages"], 381)

    def test_parse_results_summary_supports_no_results_tip(self) -> None:
        """无结果页应按 total=0 的结果页摘要返回。"""
        page = FakePage(
            {
                "div.tip-content": FakeElement("没有命中的记录"),
                "input.ivu-input.ivu-input-default": FakeElement(value="新青年"),
            },
            url="https://example.com/advanced-search",
        )

        summary = ResultParser(page).parse_results_summary()

        self.assertEqual(summary["query"], "新青年")
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["page"], "1/1")


class WanfangInteractorYearlyExportTestCase(unittest.TestCase):
    """验证截至年份逐年导出编排。"""

    def setUp(self) -> None:
        config = SimpleNamespace(
            page_timeout=1,
            action_timeout=1,
            page_change_timeout=1,
            ensure_output_dir=lambda data=None: Path(tempfile.gettempdir()) / "wanfang-yearly-tests",
            output_dir=None,
        )
        browser_manager = SimpleNamespace(is_captcha_visible=lambda page: False)
        self.interactor = WanfangSearchInteractor(
            page=FakePage({}, url="https://example.com/results"),
            config=config,
            browser_manager=browser_manager,
        )

    def test_advanced_search_dispatches_to_yearly_mode_when_date_to_without_limit(self) -> None:
        """有截至年份且未限制数量时应切到逐年模式。"""
        with (
            patch.object(self.interactor, "_run_yearly_advanced_export", return_value={"status": "success"}) as yearly,
            patch.object(self.interactor, "run_advanced_export", return_value={"status": "legacy"}) as legacy,
        ):
            result = self.interactor.advanced_search(
                query="新青年",
                date_from=None,
                date_to="2025",
                max_download=None,
            )

        self.assertEqual(result["status"], "success")
        yearly.assert_called_once()
        legacy.assert_not_called()

    def test_advanced_search_keeps_legacy_mode_when_limit_present(self) -> None:
        """指定下载数量时应保持旧的单次批量导出逻辑。"""
        with (
            patch.object(self.interactor, "_run_yearly_advanced_export", return_value={"status": "yearly"}) as yearly,
            patch.object(self.interactor, "run_advanced_export", return_value={"status": "legacy"}) as legacy,
        ):
            result = self.interactor.advanced_search(
                query="新青年",
                date_from=None,
                date_to="2025",
                max_download=100,
            )

        self.assertEqual(result["status"], "legacy")
        yearly.assert_not_called()
        legacy.assert_called_once()

    def test_build_yearly_export_tasks_uses_real_available_years(self) -> None:
        """仅应基于页面真实可选年份构造任务。"""
        tasks = self.interactor._build_yearly_export_tasks(
            query="新青年",
            available_years=["2026", "1980", "1979", "1978", "1949", "1915"],
            date_from=None,
            date_to="1980",
        )

        self.assertEqual(
            [(item["date_from"], item["date_to"]) for item in tasks],
            [("1949", "1949"), ("1978", "1978"), ("1979", "1979"), ("1980", "1980")],
        )

    def test_build_yearly_export_tasks_uses_single_year_windows_when_date_from_present(self) -> None:
        """同时传入起始年与截至年时，也应按单年窗口构造任务。"""
        tasks = self.interactor._build_yearly_export_tasks(
            query="新青年",
            available_years=["1980", "1979", "1978", "1949"],
            date_from="1978",
            date_to="1980",
        )

        self.assertEqual(
            [(item["date_from"], item["date_to"]) for item in tasks],
            [("1978", "1978"), ("1979", "1979"), ("1980", "1980")],
        )

    def test_build_yearly_export_tasks_clamps_start_year_to_1949(self) -> None:
        """起始年早于 1949 时应钳制到 1949。"""
        tasks = self.interactor._build_yearly_export_tasks(
            query="新青年",
            available_years=["1980", "1979", "1978", "1949", "1915"],
            date_from="1915",
            date_to="1980",
        )

        self.assertEqual(tasks[0]["date_from"], "1949")
        self.assertEqual(tasks[0]["date_to"], "1949")
        self.assertEqual(tasks[-1]["date_from"], "1980")
        self.assertEqual(tasks[-1]["date_to"], "1980")

    def test_run_yearly_advanced_export_skips_empty_year_and_continues(self) -> None:
        """某年无结果时应写留痕并继续后续年份。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            success_file = output_dir / "1978-merged.xlsx"
            success_file.write_text("ok", encoding="utf-8")

            self.interactor.config.ensure_output_dir = lambda data=None: output_dir
            self.interactor.export_processor = SimpleNamespace(
                merge_batch_excels=lambda excel_paths, final_file, **kwargs: str(final_file),
            )

            with (
                patch.object(self.interactor, "_prepare_yearly_progress_store", return_value=(SimpleNamespace(file_path=output_dir / "outer.json"), None)),
                patch.object(self.interactor, "_collect_available_end_years", return_value=["1949", "1978"]),
                patch.object(self.interactor, "_ensure_yearly_search_page_ready"),
                patch.object(self.interactor, "_run_single_yearly_export", side_effect=[
                    {"status": "no_results", "year": "1949"},
                    {
                        "status": "success",
                        "year": "1978",
                        "exported": 12,
                        "selected": 12,
                        "planned_download": 12,
                        "batch_count": 1,
                        "exported_batches": 1,
                        "final_file_path": str(success_file),
                        "report_file": str(output_dir / "1978-report.txt"),
                        "progress_file": str(output_dir / "1978-progress.json"),
                    },
                ]) as run_single,
                patch.object(self.interactor, "_collect_yearly_validation_outcomes", return_value=([], [])),
                patch.object(
                    self.interactor,
                    "_rebuild_yearly_output_files",
                    return_value=([str(success_file)], [str(output_dir / "1978-report.txt")]),
                ),
                patch.object(self.interactor, "_save_yearly_progress_snapshot"),
            ):
                result = self.interactor._run_yearly_advanced_export(
                    cli_params={
                        "query": "新青年",
                        "date_from": None,
                        "date_to": "1978",
                        "max_download": None,
                    },
                    progress_file=None,
                )

        self.assertEqual(run_single.call_count, 2)
        self.assertTrue(result["yearly_mode"])
        self.assertEqual(result["executed_years"], ["1949", "1978"])
        self.assertEqual(result["empty_years"], ["1949"])
        self.assertEqual(result["exported"], 12)

    def test_run_yearly_advanced_export_resumes_from_next_unfinished_year(self) -> None:
        """外层逐年进度恢复时应从下一个未完成年份继续。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            success_file = output_dir / "1979-merged.xlsx"
            success_file.write_text("ok", encoding="utf-8")
            resume_data = {
                "status": "running",
                "search_params": {
                    "query": "新青年",
                    "date_from": None,
                    "date_to": "1979",
                },
                "runtime": {
                    "available_years": ["1949", "1978", "1979"],
                    "next_year_index": 2,
                    "executed_years": ["1949", "1978"],
                    "empty_years": ["1949"],
                    "yearly_result_files": [],
                    "exported_total": 8,
                    "planned_download": 8,
                    "batch_count": 1,
                    "exported_batches": 1,
                    "current_year_progress_file": "",
                },
            }

            self.interactor.config.ensure_output_dir = lambda data=None: output_dir
            self.interactor.export_processor = SimpleNamespace(
                merge_batch_excels=lambda excel_paths, final_file, **kwargs: str(final_file),
            )

            with (
                patch.object(self.interactor, "_prepare_yearly_progress_store", return_value=(SimpleNamespace(file_path=output_dir / "outer.json"), resume_data)),
                patch.object(self.interactor, "_collect_available_end_years") as collect_years,
                patch.object(self.interactor, "_ensure_yearly_search_page_ready"),
                patch.object(self.interactor, "_run_single_yearly_export", return_value={
                    "status": "success",
                    "year": "1979",
                    "exported": 5,
                    "selected": 5,
                    "planned_download": 5,
                    "batch_count": 1,
                    "exported_batches": 1,
                    "final_file_path": str(success_file),
                    "report_file": str(output_dir / "1979-report.txt"),
                    "progress_file": str(output_dir / "1979-progress.json"),
                }) as run_single,
                patch.object(self.interactor, "_collect_yearly_validation_outcomes", return_value=([], [])),
                patch.object(
                    self.interactor,
                    "_rebuild_yearly_output_files",
                    return_value=([str(success_file)], [str(output_dir / "1979-report.txt")]),
                ),
                patch.object(self.interactor, "_save_yearly_progress_snapshot"),
            ):
                result = self.interactor._run_yearly_advanced_export(
                    cli_params={
                        "query": "新青年",
                        "date_from": None,
                        "date_to": "1979",
                        "max_download": None,
                    },
                    progress_file=None,
                )

        collect_years.assert_not_called()
        run_single.assert_called_once()
        self.assertEqual(result["executed_years"], ["1949", "1978", "1979"])
        self.assertEqual(result["empty_years"], ["1949"])

    def test_run_yearly_advanced_export_reruns_mismatched_year_before_merge(self) -> None:
        """年度汇总数量与合并表格行数不一致时应先重跑再总合并。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            success_file = output_dir / "1978-merged.xlsx"
            report_file = output_dir / "1978-report.txt"
            progress_file = output_dir / "1978-progress.json"
            task = {
                "query": "新青年",
                "year": "1978",
                "date_from": "1978",
                "date_to": "1978",
                "max_download": None,
            }

            merge_batch_excels = Mock(return_value=str(output_dir / "final.xlsx"))
            self.interactor.config.ensure_output_dir = lambda data=None: output_dir
            self.interactor.export_processor = SimpleNamespace(merge_batch_excels=merge_batch_excels)

            with (
                patch.object(self.interactor, "_prepare_yearly_progress_store", return_value=(SimpleNamespace(file_path=output_dir / "outer.json"), None)),
                patch.object(self.interactor, "_collect_available_end_years", return_value=["1978"]),
                patch.object(self.interactor, "_ensure_yearly_search_page_ready"),
                patch.object(
                    self.interactor,
                    "_run_single_yearly_export",
                    side_effect=[
                        {
                            "status": "success",
                            "year": "1978",
                            "exported": 12,
                            "selected": 12,
                            "planned_download": 12,
                            "batch_count": 1,
                            "exported_batches": 1,
                            "final_file_path": str(success_file),
                            "report_file": str(report_file),
                            "progress_file": str(progress_file),
                            "batch_report_files": [],
                        },
                        {
                            "status": "success",
                            "year": "1978",
                            "exported": 12,
                            "selected": 12,
                            "planned_download": 12,
                            "batch_count": 1,
                            "exported_batches": 1,
                            "final_file_path": str(success_file),
                            "report_file": str(report_file),
                            "progress_file": str(progress_file),
                            "batch_report_files": [],
                        },
                    ],
                ) as run_single,
                patch.object(
                    self.interactor,
                    "_collect_yearly_validation_outcomes",
                    side_effect=[
                        ([{"task": task, "year": "1978", "reported_total": 319, "actual_rows": 100}], []),
                        ([], []),
                    ],
                ) as collect_failures,
                patch.object(
                    self.interactor,
                    "_rebuild_yearly_output_files",
                    return_value=([str(success_file)], [str(report_file)]),
                ),
                patch.object(self.interactor, "_build_yearly_sub_progress_file", return_value=progress_file),
                patch.object(self.interactor, "_rerun_reset_year_for_export") as reset_year,
                patch.object(self.interactor, "_save_yearly_progress_snapshot"),
            ):
                result = self.interactor._run_yearly_advanced_export(
                    cli_params={
                        "query": "新青年",
                        "date_from": None,
                        "date_to": "1978",
                        "max_download": None,
                    },
                    progress_file=None,
                )

        self.assertEqual(run_single.call_count, 2)
        self.assertEqual(collect_failures.call_count, 2)
        reset_year.assert_called_once()
        merge_args = merge_batch_excels.call_args.args
        self.assertEqual(merge_args[0], [Path(str(success_file))])
        self.assertEqual(merge_args[1].parent, output_dir)
        self.assertTrue(merge_args[1].name.endswith("-merged.xlsx"))
        self.assertTrue(result["yearly_mode"])

    def test_run_yearly_advanced_export_stops_merge_when_validation_never_recovers(self) -> None:
        """年度结果多轮重跑后仍不一致时不应继续总合并。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            task = {
                "query": "新青年",
                "year": "1978",
                "date_from": "1978",
                "date_to": "1978",
                "max_download": None,
            }
            success_file = output_dir / "1978-merged.xlsx"
            report_file = output_dir / "1978-report.txt"
            progress_file = output_dir / "1978-progress.json"
            merge_batch_excels = Mock(return_value=str(output_dir / "final.xlsx"))
            self.interactor.config.ensure_output_dir = lambda data=None: output_dir
            self.interactor.export_processor = SimpleNamespace(merge_batch_excels=merge_batch_excels)

            with (
                patch.object(self.interactor, "_prepare_yearly_progress_store", return_value=(SimpleNamespace(file_path=output_dir / "outer.json"), None)),
                patch.object(self.interactor, "_collect_available_end_years", return_value=["1978"]),
                patch.object(self.interactor, "_ensure_yearly_search_page_ready"),
                patch.object(
                    self.interactor,
                    "_run_single_yearly_export",
                    side_effect=[
                        {
                            "status": "success",
                            "year": "1978",
                            "exported": 12,
                            "selected": 12,
                            "planned_download": 12,
                            "batch_count": 1,
                            "exported_batches": 1,
                            "final_file_path": str(output_dir / "1978-merged.xlsx"),
                            "report_file": str(output_dir / "1978-report.txt"),
                            "progress_file": str(progress_file),
                            "batch_report_files": [],
                        },
                        {
                            "status": "success",
                            "year": "1978",
                            "exported": 12,
                            "selected": 12,
                            "planned_download": 12,
                            "batch_count": 1,
                            "exported_batches": 1,
                            "final_file_path": str(output_dir / "1978-merged.xlsx"),
                            "report_file": str(output_dir / "1978-report.txt"),
                            "progress_file": str(progress_file),
                            "batch_report_files": [],
                        },
                        {
                            "status": "success",
                            "year": "1978",
                            "exported": 12,
                            "selected": 12,
                            "planned_download": 12,
                            "batch_count": 1,
                            "exported_batches": 1,
                            "final_file_path": str(output_dir / "1978-merged.xlsx"),
                            "report_file": str(output_dir / "1978-report.txt"),
                            "progress_file": str(progress_file),
                            "batch_report_files": [],
                        },
                    ],
                ),
                patch.object(
                    self.interactor,
                    "_collect_yearly_validation_outcomes",
                    side_effect=[
                        ([{"task": task, "year": "1978", "reported_total": 319, "actual_rows": 100}], []),
                        ([{"task": task, "year": "1978", "reported_total": 319, "actual_rows": 100}], []),
                        ([{"task": task, "year": "1978", "reported_total": 319, "actual_rows": 100}], []),
                    ],
                ),
                patch.object(self.interactor, "_build_yearly_sub_progress_file", return_value=progress_file),
                patch.object(self.interactor, "_rerun_reset_year_for_export"),
                patch.object(self.interactor, "_save_yearly_progress_snapshot"),
                patch.object(self.interactor, "_rebuild_yearly_output_files", return_value=([str(success_file)], [str(report_file)])),
            ):
                result = self.interactor._run_yearly_advanced_export(
                    cli_params={
                        "query": "新青年",
                        "date_from": None,
                        "date_to": "1978",
                        "max_download": None,
                    },
                    progress_file=None,
                )

        merge_batch_excels.assert_called_once()

    def test_run_yearly_advanced_export_skips_unreadable_year_and_keeps_merge(self) -> None:
        """某个年度合并表格无法读取时应记录跳过并继续总合并。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            success_file = output_dir / "1978-merged.xlsx"
            report_file = output_dir / "1978-report.txt"
            good_year_task = {
                "query": "新青年",
                "year": "1978",
                "date_from": "1978",
                "date_to": "1978",
                "max_download": None,
            }
            bad_year_task = {
                "query": "新青年",
                "year": "1979",
                "date_from": "1979",
                "date_to": "1979",
                "max_download": None,
            }
            merge_batch_excels = Mock(return_value=str(output_dir / "final.xlsx"))
            self.interactor.config.ensure_output_dir = lambda data=None: output_dir
            self.interactor.export_processor = SimpleNamespace(merge_batch_excels=merge_batch_excels)

            with (
                patch.object(self.interactor, "_prepare_yearly_progress_store", return_value=(SimpleNamespace(file_path=output_dir / "outer.json"), None)),
                patch.object(self.interactor, "_collect_available_end_years", return_value=["1978", "1979"]),
                patch.object(self.interactor, "_ensure_yearly_search_page_ready"),
                patch.object(
                    self.interactor,
                    "_run_single_yearly_export",
                    side_effect=[
                        {
                            "status": "success",
                            "year": "1978",
                            "exported": 12,
                            "selected": 12,
                            "planned_download": 12,
                            "batch_count": 1,
                            "exported_batches": 1,
                            "final_file_path": str(success_file),
                            "report_file": str(report_file),
                            "progress_file": str(output_dir / "1978-progress.json"),
                            "batch_report_files": [],
                        },
                        {
                            "status": "success",
                            "year": "1979",
                            "exported": 5,
                            "selected": 5,
                            "planned_download": 5,
                            "batch_count": 1,
                            "exported_batches": 1,
                            "final_file_path": str(output_dir / "1979-merged.xlsx"),
                            "report_file": str(output_dir / "1979-report.txt"),
                            "progress_file": str(output_dir / "1979-progress.json"),
                            "batch_report_files": [],
                        },
                    ],
                ),
                patch.object(
                    self.interactor,
                    "_collect_yearly_validation_outcomes",
                    return_value=(
                        [],
                        [{"year": "1979", "reason": "年度合并 Excel 无法读取: File is not a zip file"}],
                    ),
                ),
                patch.object(
                    self.interactor,
                    "_rebuild_yearly_output_files",
                    return_value=([str(success_file)], [str(report_file)]),
                ),
                patch.object(self.interactor, "_save_yearly_progress_snapshot"),
            ):
                result = self.interactor._run_yearly_advanced_export(
                    cli_params={
                        "query": "新青年",
                        "date_from": None,
                        "date_to": "1979",
                        "max_download": None,
                    },
                    progress_file=None,
                )

        self.assertEqual(result["skipped_years"], ["1979"])
        self.assertEqual(result["skipped_year_details"][0]["year"], "1979")
        self.assertIn("无法读取", result["skipped_year_details"][0]["reason"])
        merge_args = merge_batch_excels.call_args.args
        self.assertEqual(merge_args[0], [Path(str(success_file))])

    def test_run_single_yearly_export_reopens_advanced_search_page(self) -> None:
        """年度子任务开始前应重新打开首页并进入高级检索页。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            task = {
                "query": "新青年",
                "year": "1978",
                "date_from": "1978",
                "date_to": "1978",
                "max_download": None,
            }

            with (
                patch.object(self.interactor, "_open_advanced_search_page") as open_page,
                patch.object(self.interactor, "_wait_for_any_selector"),
                patch.object(self.interactor, "_ensure_captcha_cleared"),
                patch.object(
                    self.interactor,
                    "run_advanced_export",
                    return_value={"status": "success"},
                ) as run_export,
            ):
                self.interactor._run_single_yearly_export(
                    task=task,
                    output_dir=output_dir,
                    progress_file=output_dir / "progress.json",
                )

        open_page.assert_called_once()
        run_export.assert_called_once_with(
            cli_params=task,
            progress_file=output_dir / "progress.json",
            reuse_current_search_page=True,
        )

    def test_save_yearly_progress_snapshot_persists_current_year_context(self) -> None:
        """外层逐年进度应显式记录当前处理年份与区间。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            progress_path = Path(temp_dir) / "yearly-progress.json"

            class FakeStore:
                def __init__(self, file_path: Path) -> None:
                    self.file_path = file_path

                def save(self, state: dict) -> str:
                    self.file_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                    return str(self.file_path)

            store = FakeStore(progress_path)
            self.interactor._save_yearly_progress_snapshot(
                progress_store=store,
                status="failed",
                search_params={
                    "query": "新青年",
                    "date_from": "1978",
                    "date_to": "1980",
                },
                output_dir=Path(temp_dir),
                available_years=["1978", "1979", "1980"],
                next_year_index=1,
                executed_years=["1978"],
                empty_years=[],
                yearly_result_files=[],
                batch_report_files=[],
                yearly_report_files=[],
                empty_result_files=[],
                current_year="1979",
                current_year_date_from="1978",
                current_year_date_to="1979",
                current_year_progress_file=str(Path(temp_dir) / "year-1979" / "progress.json"),
                total=100,
                planned_download=100,
                exported_total=50,
                batch_count=2,
                exported_batches=1,
                final_file_path="",
                error=RuntimeError("翻页失败"),
            )
            data = json.loads(progress_path.read_text(encoding="utf-8"))

        self.assertEqual(data["runtime"]["current_year"], "1979")
        self.assertEqual(data["runtime"]["current_year_date_from"], "1978")
        self.assertEqual(data["runtime"]["current_year_date_to"], "1979")
        self.assertTrue(data["runtime"]["current_year_progress_file"].endswith("progress.json"))


class WanfangCliOutputTestCase(unittest.TestCase):
    """验证 CLI 输出与结果透出。"""

    def test_print_human_readable_shows_summary_report_path(self) -> None:
        """可读输出应透出汇总报告路径。"""
        data = {
            "result_type": "advanced_export",
            "query": "新青年",
            "status": "success",
            "total": 120,
            "selected": 100,
            "exported": 100,
            "planned_download": 100,
            "exported_batches": 1,
            "batch_count": 1,
            "date_range": "2020 ~ 2025",
            "url": "https://example.com/results",
            "final_file_path": "F:/temp/final.xlsx",
            "report_file": "F:/temp/report.txt",
            "progress_file": "F:/temp/progress.json",
            "resumed_from_progress": False,
        }

        with patch("builtins.print") as mock_print:
            print_human_readable(data)

        mock_print.assert_any_call("报告: F:/temp/report.txt")

    def test_cli_main_exposes_report_files_in_saved_files(self) -> None:
        """CLI 应把报告文件路径加入 saved_files。"""
        result = {
            "result_type": "advanced_export",
            "output_dir": "F:/temp",
            "file_path": "F:/temp/final.xlsx",
            "progress_file": "F:/temp/progress.json",
            "report_file": "F:/temp/report.txt",
            "batch_report_files": ["F:/temp/batch-1-report.txt"],
        }
        args = SimpleNamespace(command="advanced-search", debug=False)
        config = SimpleNamespace(save_results=True, json_only=False, ensure_output_dir=lambda _result: Path("F:/temp"))

        with (
            patch.object(_cli_module, "create_parser", return_value=SimpleNamespace(parse_args=lambda: args)),
            patch.object(_cli_module, "setup_logging"),
            patch.object(_cli_module, "build_config", return_value=config),
            patch.object(_cli_module, "run_command", return_value=result),
            patch.object(_cli_module, "save_results", return_value="F:/temp/result.json"),
            patch.object(_cli_module, "print_human_readable") as print_human_readable_mock,
        ):
            exit_code = _cli_module.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            result["saved_files"],
            {
                "json": "F:/temp/result.json",
                "export": "F:/temp/final.xlsx",
                "progress": "F:/temp/progress.json",
                "report": "F:/temp/report.txt",
                "batch_reports": ["F:/temp/batch-1-report.txt"],
            },
        )
        print_human_readable_mock.assert_called_once_with(result)


if __name__ == "__main__":
    unittest.main()
