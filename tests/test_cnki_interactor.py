"""CNKI 页面交互测试。"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
import json

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.script_loader import load_script_module

SCRIPT_DIR = ROOT_DIR / "cnki-search" / "scripts"
_exceptions_module = load_script_module(SCRIPT_DIR, "exceptions", "cnki_exceptions_module")
_interactor_module = load_script_module(
    SCRIPT_DIR,
    "cnki_search_interactor",
    "cnki_search_interactor_module",
)

CnkiSearchInteractor = _interactor_module.CnkiSearchInteractor
TimeoutError = CnkiSearchInteractor._wait_for_any_selector.__globals__["TimeoutError"]
ValidationError = CnkiSearchInteractor._prepare_progress_store.__globals__["ValidationError"]
NavigationStateError = CnkiSearchInteractor._open_advanced_search_page.__globals__["NavigationStateError"]
INTERACTOR_TIME = CnkiSearchInteractor._select_dropdown_option.__globals__["time"]


class FakeLocator:
    """模拟可等待的页面定位器。"""

    def __init__(self, visible_after: int = 0, text: str = "", attributes=None) -> None:
        self.visible_after = visible_after
        self.text = text
        self.attributes = attributes or {}
        self.wait_calls = 0
        self.click_calls: list[bool] = []
        self.scroll_calls = 0
        self.evaluate_calls: list[str] = []
        self.evaluate_payloads: list[object] = []
        self.fail_click_times = 0
        self.fail_check_times = 0
        self.checked = False

    def wait_for(self, state: str = "visible", timeout: int = 500) -> None:
        """模拟等待元素可见。"""
        del state, timeout
        self.wait_calls += 1
        if self.wait_calls <= self.visible_after:
            raise RuntimeError("元素尚未出现")

    def click(self, force: bool = False, **kwargs) -> None:
        """记录点击行为。"""
        del kwargs
        if self.fail_click_times > 0:
            self.fail_click_times -= 1
            raise RuntimeError("点击失败")
        self.click_calls.append(force)

    def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        """记录滚动行为。"""
        del timeout
        self.scroll_calls += 1

    def evaluate(self, script: str, payload=None) -> None:
        """记录脚本点击行为。"""
        self.evaluate_calls.append(script)
        self.evaluate_payloads.append(payload)

    def inner_text(self) -> str:
        """返回文本内容。"""
        return self.text

    def get_attribute(self, name: str) -> str:
        """返回属性值。"""
        return self.attributes.get(name, "")

    def check(self, force: bool = False, timeout: int = 0) -> None:
        """记录勾选行为。"""
        del force, timeout
        if self.fail_check_times > 0:
            self.fail_check_times -= 1
            raise RuntimeError("勾选失败")
        self.checked = True

    def is_checked(self) -> bool:
        """返回勾选状态。"""
        return self.checked

    def count(self) -> int:
        """返回当前定位器数量。"""
        return 1


class FakeLocatorGroup:
    """模拟定位器集合。"""

    def __init__(self, locators) -> None:
        self.locators = list(locators)

    @property
    def first(self):
        """返回第一个定位器。"""
        return self.locators[0]

    def count(self) -> int:
        """返回集合大小。"""
        return len(self.locators)

    def nth(self, index: int):
        """返回指定下标定位器。"""
        return self.locators[index]

    def filter(self, **kwargs):
        """返回过滤后的定位器集合。"""
        del kwargs
        return self


class FakePage:
    """模拟页面对象。"""

    def __init__(self, mapping: dict[str, object] | None = None, url: str = "https://example.com/export") -> None:
        self.mapping = mapping or {}
        self.url = url
        self.context = SimpleNamespace(pages=[self])
        self.wait_for_load_state_calls: list[tuple[str, int | None]] = []
        self.goto_calls: list[dict[str, object]] = []
        self.reload_calls: list[dict[str, object]] = []
        self.close_calls = 0
        self.goto_side_effect = None

    def locator(self, selector: str):
        """返回定位器。"""
        return self.mapping.get(selector, FakeLocatorGroup([]))

    def goto(self, url: str, **kwargs) -> None:
        """记录导航行为。"""
        self.goto_calls.append({"url": url, **kwargs})
        if self.goto_side_effect is not None:
            raise self.goto_side_effect
        self.url = url

    def wait_for_load_state(self, state: str = "load", timeout: int | None = None) -> None:
        """记录加载状态等待。"""
        self.wait_for_load_state_calls.append((state, timeout))

    def reload(self, **kwargs) -> None:
        """记录刷新调用。"""
        self.reload_calls.append(kwargs)

    def close(self) -> None:
        """记录关闭调用。"""
        self.close_calls += 1
        try:
            self.context.pages.remove(self)
        except Exception:
            pass


class CnkiInteractorDropdownTestCase(unittest.TestCase):
    """验证高级检索下拉框等待逻辑。"""

    def setUp(self) -> None:
        """初始化交互对象。"""
        config = SimpleNamespace(page_timeout=1)
        self.interactor = CnkiSearchInteractor(page=None, config=config, browser_manager=None)

    def test_select_dropdown_option_retries_until_option_visible(self) -> None:
        """下拉项延迟出现时应在超时内重试成功。"""
        trigger = FakeLocator()
        option = FakeLocator(visible_after=1)

        with patch.object(INTERACTOR_TIME, "sleep", return_value=None):
            self.interactor._select_dropdown_option(trigger, option, force=True)

        self.assertEqual(len(trigger.click_calls), 2)
        self.assertEqual(option.click_calls, [True])

    def test_select_dropdown_option_raises_after_timeout(self) -> None:
        """超时后仍未出现下拉项时应抛出异常。"""
        trigger = FakeLocator()
        option = FakeLocator(visible_after=999999)
        self.interactor.config.page_timeout = 0.01

        with patch.object(INTERACTOR_TIME, "sleep", return_value=None):
            with self.assertRaises(ValidationError):
                self.interactor._select_dropdown_option(trigger, option)


class CnkiInteractorDateFormTestCase(unittest.TestCase):
    """验证高级检索日期填写逻辑。"""

    def setUp(self) -> None:
        """初始化交互对象。"""
        config = SimpleNamespace(page_timeout=1)
        self.interactor = CnkiSearchInteractor(page=None, config=config, browser_manager=None)

    def test_fill_advanced_search_form_uses_year_inputs(self) -> None:
        """日期范围应写入起始年与结束年输入框。"""
        with (
            patch.object(self.interactor, "_disable_checkbox"),
            patch.object(self.interactor, "_ensure_advanced_condition_rows"),
            patch.object(self.interactor, "_set_advanced_condition"),
            patch.object(self.interactor, "_set_year_input_value") as set_year_input_value,
        ):
            self.interactor._fill_advanced_search_form(
                query="图书馆 知识服务",
                date_from="2024",
                date_to="2025",
                core_only=False,
                include_no_fulltext=False,
            )

        self.assertEqual(
            set_year_input_value.call_args_list,
            [
                call(["input[placeholder='起始年']", "input[placeholder*='起始']"], "2024"),
                call(["input[placeholder='结束年']", "input[placeholder*='结束']"], "2025"),
            ],
        )

    def test_fill_advanced_search_form_clears_start_year_when_initial_date_from_missing(self) -> None:
        """未传起始年时应主动清空起始年输入框，避免复用页面残留旧值。"""
        with (
            patch.object(self.interactor, "_disable_checkbox"),
            patch.object(self.interactor, "_ensure_advanced_condition_rows"),
            patch.object(self.interactor, "_set_advanced_condition"),
            patch.object(self.interactor, "_set_year_input_value") as set_year_input_value,
        ):
            self.interactor._fill_advanced_search_form(
                query="新青年",
                date_from=None,
                date_to="1978",
                core_only=False,
                include_no_fulltext=False,
            )

        self.assertEqual(
            set_year_input_value.call_args_list,
            [
                call(["input[placeholder='起始年']", "input[placeholder*='起始']"], ""),
                call(["input[placeholder='结束年']", "input[placeholder*='结束']"], "1978"),
            ],
        )

    def test_set_year_input_value_updates_cnki_specific_attributes_and_events(self) -> None:
        """年份输入框应同步 value/txt/condition 并触发 keyup。"""
        locator = FakeLocator()

        with patch.object(self.interactor, "_first_visible_locator", return_value=locator):
            self.interactor._set_year_input_value(
                ["input[placeholder='结束年']"],
                "1978",
            )

        self.assertEqual(locator.evaluate_payloads, ["1978"])
        self.assertIn("setAttribute('txt', normalizedValue)", locator.evaluate_calls[0])
        self.assertIn("KeyboardEvent('keyup'", locator.evaluate_calls[0])

    def test_fill_advanced_search_form_can_disable_onlyfulltext(self) -> None:
        """传入参数后应取消仅看有全文。"""
        with (
            patch.object(self.interactor, "_disable_checkbox") as disable_checkbox,
            patch.object(self.interactor, "_ensure_advanced_condition_rows"),
            patch.object(self.interactor, "_set_advanced_condition"),
            patch.object(self.interactor, "_set_year_input_value"),
        ):
            self.interactor._fill_advanced_search_form(
                query="图书馆 知识服务",
                date_from=None,
                date_to=None,
                core_only=False,
                include_no_fulltext=True,
            )

        disable_checkbox.assert_any_call("input[data-id='EN'][name='onlyChecked']")
        disable_checkbox.assert_any_call("#onlyfulltext")


class CnkiInteractorOpenPageTestCase(unittest.TestCase):
    """验证高级检索页面打开策略。"""

    def setUp(self) -> None:
        """初始化交互对象。"""
        config = SimpleNamespace(page_timeout=1)
        self.interactor = CnkiSearchInteractor(page=None, config=config, browser_manager=None)

    def test_open_advanced_search_page_skips_fallback_goto_when_click_has_navigated(self) -> None:
        """点击入口后若页面已可操作，不应再次强制 goto。"""
        browser_manager = SimpleNamespace(
            restore_session=unittest.mock.Mock(),
            is_captcha_visible=lambda page: False,
        )
        page = FakePage(
            {
                "#highSearch": FakeLocatorGroup([FakeLocator()]),
                "input[placeholder='结束年']": FakeLocatorGroup([FakeLocator()]),
            },
            url="https://example.com/home",
        )
        config = SimpleNamespace(
            page_timeout=1,
            navigation_timeout=1,
            advanced_search_url="https://example.com/advanced",
            home_url="https://example.com/home",
        )
        interactor = CnkiSearchInteractor(page=page, config=config, browser_manager=browser_manager)

        interactor._open_advanced_search_page()

        browser_manager.restore_session.assert_called_once_with(config.home_url)
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(page.wait_for_load_state_calls, [("domcontentloaded", 1000)])

    def test_open_advanced_search_page_switches_to_new_tab_and_closes_old_tab(self) -> None:
        """点击高级检索若新开页签，应切换到新页签并关闭旧页签。"""
        browser_manager = SimpleNamespace(
            restore_session=unittest.mock.Mock(),
            is_captcha_visible=lambda page: False,
            _page=None,
        )
        old_page = FakePage(
            {
                "#highSearch": FakeLocatorGroup([FakeLocator()]),
            },
            url="https://example.com/home",
        )
        new_page = FakePage(
            {
                "input[placeholder='结束年']": FakeLocatorGroup([FakeLocator()]),
            },
            url="https://example.com/advanced",
        )
        context = SimpleNamespace(pages=[old_page])
        old_page.context = context
        new_page.context = context

        link = old_page.locator("#highSearch").first

        def open_new_tab(force: bool = False, **kwargs) -> None:
            del force, kwargs
            context.pages.append(new_page)

        link.click = open_new_tab

        config = SimpleNamespace(
            page_timeout=1,
            navigation_timeout=1,
            advanced_search_url="https://example.com/advanced",
            home_url="https://example.com/home",
        )
        interactor = CnkiSearchInteractor(page=old_page, config=config, browser_manager=browser_manager)

        with patch.object(INTERACTOR_TIME, "sleep", return_value=None):
            interactor._open_advanced_search_page()

        self.assertIs(interactor.page, new_page)
        self.assertIs(browser_manager._page, new_page)
        self.assertEqual(old_page.close_calls, 1)
        self.assertEqual(new_page.wait_for_load_state_calls, [("domcontentloaded", 1000)])
        self.assertEqual(new_page.goto_calls, [])

    def test_open_advanced_search_page_raises_retryable_navigation_error_when_page_not_ready(self) -> None:
        """高级检索页未真正打开时应抛出可自动续跑的导航异常。"""
        browser_manager = SimpleNamespace(
            restore_session=unittest.mock.Mock(),
            is_captcha_visible=lambda page: False,
        )
        page = FakePage(
            {
                "#highSearch": FakeLocatorGroup([FakeLocator()]),
            },
            url="https://example.com/home",
        )
        config = SimpleNamespace(
            page_timeout=1,
            navigation_timeout=1,
            advanced_search_url="https://example.com/advanced",
            home_url="https://example.com/home",
        )
        interactor = CnkiSearchInteractor(page=page, config=config, browser_manager=browser_manager)

        with self.assertRaisesRegex(NavigationStateError, "打开统一高级检索页面失败"):
            interactor._open_advanced_search_page()

    def test_fill_advanced_search_form_checks_core_sources(self) -> None:
        """核心检索应取消全部期刊并勾选核心来源。"""
        page = SimpleNamespace(locator=None)
        self.interactor.page = page
        fake_checkbox = SimpleNamespace(count=lambda: 1, check=lambda force=True: None)

        with (
            patch.object(self.interactor, "_disable_checkbox") as disable_checkbox,
            patch.object(self.interactor, "_ensure_advanced_condition_rows"),
            patch.object(self.interactor, "_set_advanced_condition"),
            patch.object(self.interactor, "_set_year_input_value"),
            patch.object(self.interactor, "_enable_checkbox") as enable_checkbox,
        ):
            self.interactor._fill_advanced_search_form(
                query="图书馆 知识服务",
                date_from=None,
                date_to=None,
                core_only=True,
                include_no_fulltext=False,
            )

        disable_checkbox.assert_any_call("input[name='all']")
        self.assertEqual(
            enable_checkbox.call_args_list,
            [
                call("input[key='LYBSM'][value='P12']"),
                call("input[key='SI'][value='Y']"),
                call("input[key='EI'][value='Y']"),
                call("input[key='HX'][value='Y']"),
                call("input[key='CSI'][value='Y']"),
                call("input[key='CSD'][value='Y']"),
                call("input[key='AMI'][value='P13']"),
            ],
        )


class CnkiInteractorPaginationTestCase(unittest.TestCase):
    """验证结果页翻页稳定性逻辑。"""

    def setUp(self) -> None:
        """初始化交互对象。"""
        config = SimpleNamespace(page_timeout=1, action_timeout=1, page_change_timeout=2)
        browser_manager = SimpleNamespace(is_captcha_visible=lambda page: False)
        page = SimpleNamespace(url="https://example.com/results")
        self.interactor = CnkiSearchInteractor(page=page, config=config, browser_manager=browser_manager)
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"page": "1/10"})

    def test_click_next_page_link_falls_back_to_js_on_last_attempt(self) -> None:
        """最后一次重试应允许退回 JS 点击。"""
        locator = FakeLocator()
        locator.fail_click_times = 1

        self.interactor._click_next_page_link(locator, self.interactor.NEXT_PAGE_MAX_RETRIES)

        self.assertEqual(locator.scroll_calls, 1)
        self.assertEqual(locator.evaluate_calls, ["(element) => element.click()"])

    def test_goto_next_results_page_retries_after_transient_failure(self) -> None:
        """翻页等待超时后应重试，而不是立即中断。"""
        locator = FakeLocator()

        with (
            patch.object(self.interactor, "_find_next_page_link", return_value=locator),
            patch.object(self.interactor, "_first_result_title", return_value="标题A"),
            patch.object(self.interactor, "_current_sort_text", return_value="相关度"),
            patch.object(self.interactor, "_click_next_page_link") as click_next_page_link,
            patch.object(
                self.interactor,
                "_wait_for_results_changed",
                side_effect=[TimeoutError("首次等待超时"), None],
            ) as wait_for_results_changed,
            patch.object(self.interactor, "_has_results_state_changed", return_value=False),
            patch.object(self.interactor, "_dismiss_dialog_if_present") as dismiss_dialog,
            patch.object(self.interactor, "_ensure_captcha_cleared") as ensure_captcha_cleared,
            patch.object(INTERACTOR_TIME, "sleep", return_value=None),
        ):
            result = self.interactor._goto_next_results_page()

        self.assertTrue(result)
        self.assertEqual(click_next_page_link.call_args_list, [call(locator, 1), call(locator, 2)])
        self.assertEqual(wait_for_results_changed.call_count, 2)
        dismiss_dialog.assert_called_once()
        ensure_captcha_cleared.assert_called_once()

    def test_wait_for_results_changed_tolerates_transient_parse_error(self) -> None:
        """页面局部刷新时的瞬时异常不应直接导致等待失败。"""
        with (
            patch.object(
                self.interactor,
                "_has_results_state_changed",
                side_effect=[RuntimeError("页面刷新中"), True],
            ),
            patch.object(INTERACTOR_TIME, "sleep", return_value=None),
        ):
            self.interactor._wait_for_results_changed(
                previous_url="https://example.com/results",
                previous_page="1/10",
                previous_title="标题A",
                previous_sort="相关度",
                timeout=1,
            )

    def test_ensure_checkbox_checked_falls_back_to_js(self) -> None:
        """常规勾选失败时应退回 JS 勾选。"""
        checkbox = FakeLocator()
        checkbox.fail_check_times = 1

        def mark_checked(script: str) -> None:
            checkbox.evaluate_calls.append(script)
            checkbox.checked = True

        checkbox.evaluate = mark_checked

        self.interactor._ensure_checkbox_checked(checkbox, selector="#selectCheckAll1")

        self.assertEqual(checkbox.scroll_calls, 1)
        self.assertEqual(len(checkbox.evaluate_calls), 1)
        self.assertTrue(checkbox.is_checked())

    def test_select_rows_on_current_page_uses_stable_select_all_checkbox(self) -> None:
        """整页全选时应走稳定复选框勾选逻辑。"""
        select_all_checkbox = FakeLocator()
        row_checkboxes = [FakeLocator(), FakeLocator()]

        def locator(selector: str):
            if selector == "#selectCheckAll1":
                return FakeLocatorGroup([select_all_checkbox])
            if selector == ".result-table-list tbody input.cbItem":
                return FakeLocatorGroup(row_checkboxes)
            raise AssertionError(f"unexpected selector: {selector}")

        self.interactor.page = SimpleNamespace(locator=locator)

        with (
            patch.object(self.interactor, "_ensure_checkbox_checked") as ensure_checkbox_checked,
            patch.object(self.interactor, "_count_checked_rows", return_value=2),
            patch.object(self.interactor, "_find_unchecked_row_indexes", return_value=[]),
        ):
            self.interactor._select_rows_on_current_page(row_offset=0, page_target_count=2, row_count=2)

        ensure_checkbox_checked.assert_called_once_with(select_all_checkbox, selector="#selectCheckAll1")

    def test_select_batch_results_tolerates_page_shortfall_in_full_export_mode(self) -> None:
        """全量导出模式下不应为了补足缺口继续跨页凑满批次目标。"""
        rows = [FakeLocator() for _ in range(50)]

        def locator(selector: str):
            if selector == ".result-table-list tbody tr":
                return FakeLocatorGroup(rows)
            raise AssertionError(f"unexpected selector: {selector}")

        self.interactor.page = SimpleNamespace(locator=locator)

        with (
            patch.object(self.interactor, "_wait_for_results_ready"),
            patch.object(self.interactor, "_select_rows_on_current_page", side_effect=[47, 50]),
            patch.object(self.interactor, "_current_results_page_number", side_effect=[1, 2]),
            patch.object(self.interactor, "_goto_next_results_page", return_value=True) as goto_next_results_page,
        ):
            result = self.interactor._select_batch_results(export_limit=100, row_offset=0, strict_target=False)

        self.assertEqual(result["selected_count"], 97)
        self.assertEqual(result["next_row_offset"], 50)
        self.assertEqual(result["page_row_count"], 50)
        self.assertEqual(result["start_page"], 1)
        self.assertEqual(result["end_page"], 2)
        goto_next_results_page.assert_called_once()

    def test_select_batch_results_keeps_topping_up_in_strict_mode(self) -> None:
        """限定数量模式下应继续向后补足目标数量。"""
        rows = [FakeLocator() for _ in range(50)]

        def locator(selector: str):
            if selector == ".result-table-list tbody tr":
                return FakeLocatorGroup(rows)
            raise AssertionError(f"unexpected selector: {selector}")

        self.interactor.page = SimpleNamespace(locator=locator)

        with (
            patch.object(self.interactor, "_wait_for_results_ready"),
            patch.object(self.interactor, "_select_rows_on_current_page", side_effect=[47, 50, 3]),
            patch.object(self.interactor, "_current_results_page_number", side_effect=[1, 2, 3]),
            patch.object(self.interactor, "_goto_next_results_page", return_value=True) as goto_next_results_page,
        ):
            result = self.interactor._select_batch_results(export_limit=100, row_offset=0, strict_target=True)

        self.assertEqual(result["selected_count"], 100)
        self.assertEqual(result["next_row_offset"], 3)
        self.assertEqual(result["page_row_count"], 50)
        self.assertEqual(result["start_page"], 1)
        self.assertEqual(result["end_page"], 3)
        self.assertEqual(goto_next_results_page.call_count, 2)

    def test_select_batch_results_returns_reached_end_when_last_page_exhausted(self) -> None:
        """续跑游标已落在末页末尾时应正常结束，而不是抛出失败。"""
        rows = [FakeLocator() for _ in range(50)]

        def locator(selector: str):
            if selector == ".result-table-list tbody tr":
                return FakeLocatorGroup(rows)
            raise AssertionError(f"unexpected selector: {selector}")

        self.interactor.page = SimpleNamespace(locator=locator)

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

    def test_restore_results_position_advances_until_target_page(self) -> None:
        """恢复执行前应顺序翻页到进度文件记录页码。"""
        pages = iter(
            [
                {"current_page": 1, "page": "1/10"},
                {"current_page": 2, "page": "2/10"},
                {"current_page": 3, "page": "3/10"},
            ]
        )
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: next(pages))

        with (
            patch.object(self.interactor, "_find_resume_target_page_link", return_value=None),
            patch.object(self.interactor, "_goto_next_results_page", return_value=True) as goto_next_results_page,
        ):
            self.interactor._restore_results_position(target_page=3)

        self.assertEqual(goto_next_results_page.call_count, 2)

    def test_find_resume_target_page_link_prefers_farthest_visible_page(self) -> None:
        """恢复页码时应优先选择当前可见范围内最大的数字页。"""
        home_link = FakeLocator(text="首页", attributes={"data-curpage": "1"})
        prev_link = FakeLocator(text="上一页", attributes={"data-curpage": "12"})
        page_12 = FakeLocator(text="12", attributes={"data-curpage": "12"})
        current_page = FakeLocator(text="13", attributes={"data-curpage": "13", "class": "cur"})
        page_15 = FakeLocator(text="15", attributes={"data-curpage": "15"})
        page_17 = FakeLocator(text="17", attributes={"data-curpage": "17"})
        next_link = FakeLocator(text="下一页", attributes={"data-curpage": "14"})

        def locator(selector: str):
            if selector == ".pages a[data-curpage]":
                return FakeLocatorGroup([home_link, prev_link, page_12, current_page, page_15, page_17, next_link])
            raise AssertionError(f"unexpected selector: {selector}")

        self.interactor.page = SimpleNamespace(locator=locator)

        target_link = self.interactor._find_resume_target_page_link(current_page=13, target_page=30)

        self.assertIs(target_link, page_17)

    def test_restore_results_position_prefers_visible_page_jump_before_next(self) -> None:
        """恢复执行时应先尝试点击可见数字页，再回退到下一页。"""
        jump_link = FakeLocator(text="17", attributes={"data-curpage": "17"})
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


class CnkiInteractorExportTestCase(unittest.TestCase):
    """验证导出页稳定性逻辑。"""

    def setUp(self) -> None:
        """初始化交互对象。"""
        config = SimpleNamespace(page_timeout=1, action_timeout=1, page_change_timeout=1)
        browser_manager = SimpleNamespace(is_captcha_visible=lambda page: False)
        self.interactor = CnkiSearchInteractor(
            page=FakePage(url="https://example.com/results"),
            config=config,
            browser_manager=browser_manager,
        )

    def test_wait_for_export_page_ready_accepts_export_sidebar_marker(self) -> None:
        """导出页出现左侧格式栏后应判定为就绪。"""
        export_page = FakePage(
            {
                ".export-sidebar-a": FakeLocatorGroup([FakeLocator()]),
            }
        )

        result = self.interactor._wait_for_export_page_ready(export_page)

        self.assertIs(result, export_page)
        self.assertEqual(export_page.wait_for_load_state_calls, [("domcontentloaded", 1000)])

    def test_export_selected_results_does_not_force_reload_before_waiting(self) -> None:
        """导出页已打开时不应先执行强制刷新。"""
        export_page = FakePage(
            {
                ".export-sidebar-a": FakeLocatorGroup([FakeLocator()]),
            }
        )

        with (
            patch.object(self.interactor, "_open_export_menu"),
            patch.object(self.interactor, "_open_custom_export_page", return_value=export_page),
            patch.object(self.interactor, "_wait_for_export_page_ready", return_value=export_page),
            patch.object(self.interactor, "_click_link_by_text"),
            patch.object(
                self.interactor,
                "_download_from_export_page",
                side_effect=["F:/temp/metadata.xls", "F:/temp/reference.txt"],
            ),
            patch.object(self.interactor, "_click_first_available", return_value=True),
        ):
            result = self.interactor._export_selected_results("新青年", 1, Path("F:/temp"))

        self.assertEqual(result, {"excel": "F:/temp/metadata.xls", "txt": "F:/temp/reference.txt"})
        self.assertEqual(export_page.reload_calls, [])
        self.assertEqual(export_page.close_calls, 1)

    def test_click_link_by_text_retries_until_link_visible(self) -> None:
        """导出页链接延迟出现时应在超时内重试成功。"""
        delayed_link = FakeLocator(visible_after=1)
        export_page = FakePage({"a": FakeLocatorGroup([delayed_link])})

        with patch.object(INTERACTOR_TIME, "sleep", return_value=None):
            self.interactor._click_link_by_text("全选", page=export_page)

        self.assertEqual(delayed_link.click_calls, [False])


class CnkiInteractorYearlyExportTestCase(unittest.TestCase):
    """验证截至年份逐年导出编排。"""

    def setUp(self) -> None:
        """初始化交互对象。"""
        config = SimpleNamespace(
            page_timeout=1,
            action_timeout=1,
            page_change_timeout=1,
            ensure_output_dir=lambda data=None: Path(tempfile.gettempdir()) / "cnki-yearly-tests",
            output_dir=None,
        )
        browser_manager = SimpleNamespace(is_captcha_visible=lambda page: False)
        self.interactor = CnkiSearchInteractor(
            page=FakePage(url="https://example.com/results"),
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
                core_only=False,
                include_no_fulltext=False,
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
                core_only=False,
                include_no_fulltext=False,
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
            core_only=False,
            include_no_fulltext=False,
        )

        self.assertEqual(
            [(item["date_from"], item["date_to"]) for item in tasks],
            [("1949", "1949"), ("1978", "1978"), ("1979", "1979"), ("1980", "1980")],
        )

    def test_build_yearly_export_tasks_uses_single_year_windows_when_date_from_present(self) -> None:
        """同时传入起始年与截至年时，也应按单年窗口逐年构造任务。"""
        tasks = self.interactor._build_yearly_export_tasks(
            query="新青年",
            available_years=["1980", "1979", "1978", "1949"],
            date_from="1978",
            date_to="1980",
            core_only=False,
            include_no_fulltext=False,
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
            core_only=False,
            include_no_fulltext=False,
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
                merge_batch_excels=lambda excel_paths, final_file: str(final_file),
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
                        "core_only": False,
                        "include_no_fulltext": False,
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
                    "core_only": False,
                    "include_no_fulltext": False,
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
                merge_batch_excels=lambda excel_paths, final_file: str(final_file),
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
                        "core_only": False,
                        "include_no_fulltext": False,
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
                "core_only": False,
                "include_no_fulltext": False,
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
                patch.object(self.interactor, "_cleanup_year_output_dir") as cleanup_dir,
                patch.object(self.interactor, "_save_yearly_progress_snapshot"),
            ):
                result = self.interactor._run_yearly_advanced_export(
                    cli_params={
                        "query": "新青年",
                        "date_from": None,
                        "date_to": "1978",
                        "core_only": False,
                        "include_no_fulltext": False,
                        "max_download": None,
                    },
                    progress_file=None,
                )

        self.assertEqual(run_single.call_count, 2)
        self.assertEqual(collect_failures.call_count, 2)
        cleanup_dir.assert_called_once_with(output_dir, task)
        merge_args = merge_batch_excels.call_args.args
        self.assertEqual(merge_args[0], [Path(str(success_file))])
        self.assertEqual(merge_args[1].parent, output_dir)
        self.assertTrue(merge_args[1].name.endswith("-merged.xlsx"))
        self.assertTrue(result["yearly_mode"])

    def test_run_single_yearly_export_reuses_current_search_page(self) -> None:
        """年度子任务应直接复用当前高级检索页，不再重新开页。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            task = {
                "query": "新青年",
                "year": "1978",
                "date_from": "1978",
                "date_to": "1978",
                "core_only": False,
                "include_no_fulltext": False,
                "max_download": None,
            }
            self.interactor._is_advanced_search_page = lambda page: True

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

        open_page.assert_not_called()
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
                    "core_only": False,
                    "include_no_fulltext": False,
                },
                output_dir=Path(temp_dir),
                available_years=["1978", "1979", "1980"],
                next_year_index=1,
                current_year="1979",
                current_year_date_from="1978",
                current_year_date_to="1979",
                executed_years=["1978"],
                empty_years=[],
                yearly_result_files=[],
                batch_report_files=[],
                yearly_report_files=[],
                empty_result_files=[],
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


if __name__ == "__main__":
    unittest.main()
