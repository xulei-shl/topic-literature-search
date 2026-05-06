"""CNKI 页面交互测试。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "cnki-search" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from exceptions import TimeoutError, ValidationError
from interactor import CnkiSearchInteractor


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

    def evaluate(self, script: str) -> None:
        """记录脚本点击行为。"""
        self.evaluate_calls.append(script)

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

        with patch("interactor.time.sleep", return_value=None):
            self.interactor._select_dropdown_option(trigger, option, force=True)

        self.assertEqual(len(trigger.click_calls), 2)
        self.assertEqual(option.click_calls, [True])

    def test_select_dropdown_option_raises_after_timeout(self) -> None:
        """超时后仍未出现下拉项时应抛出异常。"""
        trigger = FakeLocator()
        option = FakeLocator(visible_after=999999)
        self.interactor.config.page_timeout = 0.01

        with patch("interactor.time.sleep", return_value=None):
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
            patch.object(self.interactor, "_set_input_value") as set_input_value,
        ):
            self.interactor._fill_advanced_search_form(
                query="图书馆 知识服务",
                date_from="2024",
                date_to="2025",
                core_only=False,
                include_no_fulltext=False,
            )

        self.assertEqual(
            set_input_value.call_args_list,
            [
                call(["input[placeholder='起始年']", "input[placeholder*='起始']"], "2024"),
                call(["input[placeholder='结束年']", "input[placeholder*='结束']"], "2025"),
            ],
        )

    def test_fill_advanced_search_form_can_disable_onlyfulltext(self) -> None:
        """传入参数后应取消仅看有全文。"""
        with (
            patch.object(self.interactor, "_disable_checkbox") as disable_checkbox,
            patch.object(self.interactor, "_ensure_advanced_condition_rows"),
            patch.object(self.interactor, "_set_advanced_condition"),
            patch.object(self.interactor, "_set_input_value"),
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

    def test_fill_advanced_search_form_checks_core_sources(self) -> None:
        """核心检索应取消全部期刊并勾选核心来源。"""
        page = SimpleNamespace(locator=None)
        self.interactor.page = page
        fake_checkbox = SimpleNamespace(count=lambda: 1, check=lambda force=True: None)

        with (
            patch.object(self.interactor, "_disable_checkbox") as disable_checkbox,
            patch.object(self.interactor, "_ensure_advanced_condition_rows"),
            patch.object(self.interactor, "_set_advanced_condition"),
            patch.object(self.interactor, "_set_input_value"),
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
            patch("interactor.time.sleep", return_value=None),
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
            patch("interactor.time.sleep", return_value=None),
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

        with patch.object(self.interactor, "_ensure_checkbox_checked") as ensure_checkbox_checked:
            self.interactor._select_rows_on_current_page(row_offset=0, page_target_count=2, row_count=2)

        ensure_checkbox_checked.assert_called_once_with(select_all_checkbox, selector="#selectCheckAll1")

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


if __name__ == "__main__":
    unittest.main()
