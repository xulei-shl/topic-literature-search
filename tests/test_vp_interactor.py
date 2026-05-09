"""维普页面交互测试。"""

import json
import sys
import tempfile
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.script_loader import load_script_module

SCRIPT_DIR = ROOT_DIR / "vp-search" / "scripts"
_exceptions_module = load_script_module(SCRIPT_DIR, "exceptions", "vp_exceptions_module")
_interactor_module = load_script_module(
    SCRIPT_DIR,
    "vp_search_interactor",
    "vp_search_interactor_module",
)
_cli_module = load_script_module(SCRIPT_DIR, "cli", "vp_cli_module")
_utils_module = load_script_module(SCRIPT_DIR, "utils", "vp_utils_module")
_result_parser_module = load_script_module(SCRIPT_DIR, "result_parser", "vp_result_parser_module")

VpSearchInteractor = _interactor_module.VpSearchInteractor
VpCliMain = _cli_module.main
print_human_readable = _utils_module.print_human_readable
TimeoutError = VpSearchInteractor._wait_for_any_selector.__globals__["TimeoutError"]
ValidationError = VpSearchInteractor._prepare_progress_store.__globals__["ValidationError"]
NavigationStateError = VpSearchInteractor._open_advanced_search_page.__globals__["NavigationStateError"]
ResultParser = _result_parser_module.ResultParser
INTERACTOR_TIME = VpSearchInteractor._select_dropdown_option.__globals__["time"]


class FakeLocator:
    """模拟可等待的页面定位器。"""

    def __init__(
        self,
        visible_after: int = 0,
        count_value: int = 1,
        text: str = "",
        class_name: str = "",
        checked: bool = False,
        visible: bool = True,
        attributes: dict[str, str] | None = None,
        child_locators: dict[str, "FakeLocator"] | None = None,
        on_click=None,
    ) -> None:
        self.visible_after = visible_after
        self._count_value = count_value
        self.text = text
        self.class_name = class_name
        self.checked = checked
        self.visible = visible
        self.attributes = attributes or {}
        self.child_locators = child_locators or {}
        self.on_click = on_click
        self.wait_calls = 0
        self.click_calls: list[bool] = []
        self.check_calls = 0
        self.scroll_calls = 0
        self.evaluate_calls: list[str] = []
        self.fail_click_times = 0

    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        return self._count_value

    def nth(self, index: int) -> "FakeLocator":
        del index
        return self

    def locator(self, selector: str) -> "FakeLocator":
        return self.child_locators.get(selector, FakeLocator(count_value=0))

    def filter(self, has_text=None) -> "FakeLocator":
        del has_text
        return self

    def wait_for(self, state: str = "visible", timeout: int = 500) -> None:
        del state, timeout
        self.wait_calls += 1
        if self.wait_calls <= self.visible_after:
            raise RuntimeError("元素尚未出现")

    def click(self, force: bool = False, **kwargs) -> None:
        del kwargs
        if self.fail_click_times > 0:
            self.fail_click_times -= 1
            raise RuntimeError("点击失败")
        self.click_calls.append(force)
        if self.on_click is not None:
            self.on_click(self)

    def fill(self, value: str) -> None:
        self.text = value

    def is_checked(self) -> bool:
        return self.checked

    def is_visible(self) -> bool:
        return self.visible

    def check(self, force: bool = False, timeout: int = 0) -> None:
        del force, timeout
        self.check_calls += 1
        self.checked = True

    def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        del timeout
        self.scroll_calls += 1

    def evaluate(self, script: str, *args) -> None:
        del args
        self.evaluate_calls.append(script)
        if "element.checked = true" in script:
            self.checked = True

    def get_attribute(self, name: str) -> str:
        if name == "class":
            return self.class_name
        return self.attributes.get(name, "")

    def inner_text(self) -> str:
        return self.text


class FakeCheckboxGroup:
    """模拟复选框列表。"""

    def __init__(self, size: int) -> None:
        self.items = [FakeLocator() for _ in range(size)]

    def count(self) -> int:
        return len(self.items)

    def nth(self, index: int) -> FakeLocator:
        return self.items[index]


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


class FakePage:
    """模拟页面对象。"""

    def __init__(self, mapping: dict[str, object], url: str = "https://example.com/results") -> None:
        self.mapping = mapping
        self.url = url
        self.context = SimpleNamespace(pages=[self])
        self.wait_for_load_state_calls: list[tuple[str, int | None]] = []
        self.go_back_calls: list[int | None] = []
        self.goto_calls: list[tuple[str, int | None]] = []
        self.close_calls = 0

    def locator(self, selector: str):
        return self.mapping.get(selector, FakeLocator(count_value=0))

    def query_selector(self, selector: str):
        return self.mapping.get(selector)

    def wait_for_load_state(self, state: str = "load", timeout: int | None = None) -> None:
        self.wait_for_load_state_calls.append((state, timeout))

    def goto(self, url: str, timeout: int | None = None) -> None:
        self.url = url
        self.goto_calls.append((url, timeout))

    def go_back(self, timeout: int | None = None) -> None:
        self.go_back_calls.append(timeout)

    def close(self) -> None:
        self.close_calls += 1

    def expect_download(self, timeout: int = 0):
        del timeout
        return FakeDownloadContext(FakeDownload())


class FakeDownload:
    """模拟下载对象。"""

    def __init__(self, suggested_filename: str = "download.txt") -> None:
        self.suggested_filename = suggested_filename
        self.saved_paths: list[str] = []

    def save_as(self, path: str) -> None:
        self.saved_paths.append(path)


class FakeDownloadContext:
    """模拟下载上下文管理器。"""

    def __init__(self, download: FakeDownload) -> None:
        self.value = download

    def __enter__(self) -> "FakeDownloadContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False


class VpInteractorFormTestCase(unittest.TestCase):
    """验证高级检索表单逻辑。"""

    def setUp(self) -> None:
        config = SimpleNamespace(page_timeout=1)
        self.interactor = VpSearchInteractor(page=None, config=config, browser_manager=None)

    def test_fill_advanced_search_form_uses_year_selects(self) -> None:
        with (
            patch.object(self.interactor, "_set_advanced_condition"),
            patch.object(self.interactor, "_set_native_select_value") as set_native_select_value,
        ):
            self.interactor._fill_advanced_search_form(
                query="新青年",
                date_from="2020",
                date_to="2025",
                core_only=False,
            )

        self.assertEqual(
            set_native_select_value.call_args_list,
            [call("#basic_beginYear", "2020"), call("#basic_endYear", "2025")],
        )

    def test_fill_advanced_search_form_explicitly_overwrites_missing_years(self) -> None:
        with (
            patch.object(self.interactor, "_set_advanced_condition"),
            patch.object(self.interactor, "_set_year_select_value") as set_year_select_value,
        ):
            self.interactor._fill_advanced_search_form(
                query="新青年",
                date_from=None,
                date_to="1978",
                core_only=False,
            )

        self.assertEqual(
            set_year_select_value.call_args_list,
            [call("#basic_beginYear", None), call("#basic_endYear", "1978")],
        )

    def test_fill_advanced_search_form_sets_core_sources(self) -> None:
        with (
            patch.object(self.interactor, "_set_advanced_condition"),
            patch.object(self.interactor, "_set_year_select_value"),
            patch.object(self.interactor, "_disable_checkbox") as disable_checkbox,
            patch.object(self.interactor, "_enable_checkbox") as enable_checkbox,
        ):
            self.interactor._fill_advanced_search_form(
                query="新青年",
                date_from=None,
                date_to=None,
                core_only=True,
            )

        disable_checkbox.assert_called_once_with("input[name='basic_journalRange'][title='全部期刊']")
        self.assertEqual(
            enable_checkbox.call_args_list,
            [
                call("input[name='basic_journalRange'][title='北大核心期刊']"),
                call("input[name='basic_journalRange'][title='EI来源期刊']"),
                call("input[name='basic_journalRange'][title='SCIE期刊']"),
                call("input[name='basic_journalRange'][title='CAS来源期刊']"),
                call("input[name='basic_journalRange'][title='CSCD期刊']"),
                call("input[name='basic_journalRange'][title='CSSCI期刊']"),
            ],
        )

    def test_set_advanced_condition_uses_or_and_field_selection(self) -> None:
        with (
            patch.object(self.interactor, "_get_advanced_condition_row", return_value=SimpleNamespace()),
            patch.object(self.interactor, "_select_dropdown_option") as select_dropdown_option,
        ):
            fake_input = FakeLocator()
            self.interactor.page = SimpleNamespace(locator=lambda selector: fake_input)
            self.interactor._set_advanced_condition(1, "摘要", "新青年", logic="OR")

        self.assertEqual(
            select_dropdown_option.call_args_list,
            [call(unittest.mock.ANY, "OR", display_text="或"), call(unittest.mock.ANY, "摘要", display_text="摘要")],
        )
        self.assertEqual(fake_input.click_calls, [])


class VpInteractorSelectionTestCase(unittest.TestCase):
    """验证勾选与清空逻辑。"""

    def setUp(self) -> None:
        config = SimpleNamespace(page_timeout=1)
        self.interactor = VpSearchInteractor(page=None, config=config, browser_manager=None)

    def test_clear_selected_results_confirms_dialog(self) -> None:
        clear_link = FakeLocator()
        page = FakePage(
            {
                "span.selected-count a[title='清空已选文章']": clear_link,
                "span.selected-count a": FakeLocator(count_value=0),
            }
        )
        self.interactor.page = page

        with patch.object(self.interactor, "_dismiss_confirm_dialog_if_present") as dismiss_dialog:
            self.interactor._clear_selected_results()

        self.assertEqual(clear_link.click_calls, [False])
        dismiss_dialog.assert_called_once()

    def test_select_rows_on_current_page_uses_precise_range_for_partial_page(self) -> None:
        row_checkboxes = [FakeLocator(attributes={"name": "selectArticle"}) for _ in range(10)]
        select_all = FakeLocator(attributes={"name": "selectArticleAll"})
        page_size_active = FakeLocator(attributes={"data-count": "10"})
        page = FakePage(
            {
                "#selectPageSize a.active[data-count]": page_size_active,
                ".search-list input[type='checkbox']": FakeLocatorGroup([select_all, *row_checkboxes]),
                "input[name='selectArticleAll']": select_all,
            }
        )
        self.interactor.page = page
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 1})
        checkbox_items = self.interactor._current_page_checkbox_items()

        with (
            patch.object(self.interactor, "_ensure_checkbox_checked") as ensure_checkbox_checked,
            patch.object(self.interactor, "_count_checked_rows", return_value=3),
        ):
            self.interactor._select_rows_on_current_page(
                checkbox_items=checkbox_items,
                row_offset=2,
                page_target_count=3,
                row_count=len(checkbox_items),
                selected_before_page=0,
            )

        self.assertEqual(
            ensure_checkbox_checked.call_args_list,
            [
                call(row_checkboxes[2], selector="result_checkbox[2]"),
                call(row_checkboxes[3], selector="result_checkbox[3]"),
                call(row_checkboxes[4], selector="result_checkbox[4]"),
            ],
        )

    def test_select_rows_on_current_page_partial_page_does_not_clear_current_page_selection(self) -> None:
        checkbox_group = FakeCheckboxGroup(10)

        with (
            patch.object(self.interactor, "_clear_page_selection") as clear_page_selection,
            patch.object(self.interactor, "_select_rows_incrementally") as select_rows_incrementally,
            patch.object(self.interactor, "_count_checked_rows", return_value=3),
        ):
            self.interactor._select_rows_on_current_page(
                checkbox_items=checkbox_group.items,
                row_offset=0,
                page_target_count=3,
                row_count=10,
                selected_before_page=100,
            )

        clear_page_selection.assert_not_called()
        select_rows_incrementally.assert_called_once_with(
            checkbox_items=checkbox_group.items,
            row_offset=0,
            page_target_count=3,
            selected_before_page=0,
        )

    def test_select_rows_on_current_page_falls_back_when_select_all_count_is_abnormal(self) -> None:
        checkbox_group = FakeCheckboxGroup(50)
        select_all = FakeLocator()
        page = FakePage({"input[name='selectArticleAll']": select_all})
        self.interactor.page = page

        with (
            patch.object(self.interactor, "_extract_selected_count", side_effect=[120, 50]),
            patch.object(self.interactor, "_ensure_checkbox_checked") as ensure_checkbox_checked,
            patch.object(self.interactor, "_disable_checkbox") as disable_checkbox,
            patch.object(self.interactor, "_select_rows_incrementally") as select_rows_incrementally,
            patch.object(self.interactor, "_count_checked_rows", return_value=50),
        ):
            self.interactor._select_rows_on_current_page(
                checkbox_items=checkbox_group.items,
                row_offset=0,
                page_target_count=50,
                row_count=50,
                selected_before_page=50,
            )

        ensure_checkbox_checked.assert_called_once_with(
            select_all,
            selector="input[name='selectArticleAll']",
        )
        disable_checkbox.assert_called_once_with("input[name='selectArticleAll']")
        select_rows_incrementally.assert_called_once_with(
            checkbox_items=checkbox_group.items,
            row_offset=0,
            page_target_count=50,
            selected_before_page=0,
        )

    def test_select_rows_on_current_page_allows_select_all_after_previous_page_selected(self) -> None:
        checkbox_group = FakeCheckboxGroup(50)
        select_all = FakeLocator()
        page = FakePage({"input[name='selectArticleAll']": select_all})
        self.interactor.page = page

        with (
            patch.object(self.interactor, "_extract_selected_count", return_value=100),
            patch.object(self.interactor, "_ensure_checkbox_checked") as ensure_checkbox_checked,
        ):
            self.interactor._select_rows_on_current_page(
                checkbox_items=checkbox_group.items,
                row_offset=0,
                page_target_count=50,
                row_count=50,
                selected_before_page=50,
            )

        ensure_checkbox_checked.assert_called_once_with(
            select_all,
            selector="input[name='selectArticleAll']",
        )

    def test_select_rows_on_current_page_skips_select_all_for_incomplete_page_sample(self) -> None:
        checkbox_group = FakeCheckboxGroup(3)
        page_size_active = FakeLocator(attributes={"data-count": "50"})
        self.interactor.page = FakePage({"#selectPageSize a.active[data-count]": page_size_active})

        with (
            patch.object(self.interactor, "_try_select_all_on_current_page") as try_select_all_on_current_page,
            patch.object(self.interactor, "_select_rows_incrementally") as select_rows_incrementally,
            patch.object(self.interactor, "_count_checked_rows", return_value=3),
        ):
            self.interactor._select_rows_on_current_page(
                checkbox_items=checkbox_group.items,
                row_offset=0,
                page_target_count=3,
                row_count=3,
                selected_before_page=300,
            )

        try_select_all_on_current_page.assert_not_called()
        select_rows_incrementally.assert_called_once_with(
            checkbox_items=checkbox_group.items,
            row_offset=0,
            page_target_count=3,
            selected_before_page=0,
        )

    def test_select_rows_incrementally_checks_exact_target_range(self) -> None:
        checkbox_group = FakeCheckboxGroup(5)

        with patch.object(self.interactor, "_ensure_checkbox_checked") as ensure_checkbox_checked:
            self.interactor._select_rows_incrementally(
                checkbox_items=checkbox_group.items,
                row_offset=0,
                page_target_count=3,
                selected_before_page=50,
            )

        self.assertEqual(
            ensure_checkbox_checked.call_args_list,
            [
                call(checkbox_group.items[0], selector="result_checkbox[0]"),
                call(checkbox_group.items[1], selector="result_checkbox[1]"),
                call(checkbox_group.items[2], selector="result_checkbox[2]"),
            ],
        )

    def test_select_batch_results_uses_visible_checkbox_count_for_pagination(self) -> None:
        all_checkbox_group = FakeCheckboxGroup(304)
        page_size_active = FakeLocator(attributes={"data-count": "50"})
        self.interactor.page = FakePage({"#selectPageSize a.active[data-count]": page_size_active})
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 1})
        for index, checkbox in enumerate(all_checkbox_group.items):
            checkbox.visible = False
            checkbox.child_locators = {
                "xpath=following-sibling::div[contains(@class,'layui-form-checkbox')][1]": FakeLocator(
                    count_value=1,
                    visible=index < 50,
                )
            }

        with (
            patch.object(self.interactor, "_wait_for_results_ready"),
            patch.object(self.interactor, "_result_checkbox_locator", return_value=all_checkbox_group),
            patch.object(self.interactor, "_select_rows_on_current_page", side_effect=[50, 50]) as select_rows_on_current_page,
            patch.object(self.interactor, "_goto_next_results_page", return_value=True) as goto_next_results_page,
        ):
            selection = self.interactor._select_batch_results(export_limit=100, row_offset=0, strict_target=True)

        self.assertEqual(select_rows_on_current_page.call_count, 2)
        first_call = select_rows_on_current_page.call_args_list[0].kwargs
        second_call = select_rows_on_current_page.call_args_list[1].kwargs
        self.assertEqual(len(first_call["checkbox_items"]), 50)
        self.assertEqual(first_call["row_offset"], 0)
        self.assertEqual(first_call["page_target_count"], 50)
        self.assertEqual(first_call["row_count"], 50)
        self.assertEqual(first_call["selected_before_page"], 0)
        self.assertEqual(len(second_call["checkbox_items"]), 50)
        self.assertEqual(second_call["row_offset"], 0)
        self.assertEqual(second_call["page_target_count"], 50)
        self.assertEqual(second_call["row_count"], 50)
        self.assertEqual(second_call["selected_before_page"], 50)
        goto_next_results_page.assert_called_once()
        self.assertEqual(selection["selected_count"], 100)
        self.assertEqual(selection["page_row_count"], 50)

    def test_select_batch_results_tolerates_page_shortfall_in_full_export_mode(self) -> None:
        """全量导出模式下不应为了补足缺口继续跨页追数。"""
        checkbox_items = [FakeLocator(attributes={"name": "selectArticle"}) for _ in range(50)]
        current_pages = iter([1, 2])

        with (
            patch.object(self.interactor, "_wait_for_results_ready"),
            patch.object(self.interactor, "_current_page_checkbox_items", side_effect=[checkbox_items, checkbox_items]),
            patch.object(self.interactor, "_select_rows_on_current_page", side_effect=[47, 50]) as select_rows_on_current_page,
            patch.object(
                self.interactor,
                "parser",
                SimpleNamespace(parse_results_summary=lambda: {"current_page": next(current_pages)}),
            ),
            patch.object(self.interactor, "_goto_next_results_page", return_value=True) as goto_next_results_page,
            patch.object(INTERACTOR_TIME, "sleep", return_value=None),
        ):
            selection = self.interactor._select_batch_results(export_limit=100, row_offset=0, strict_target=False)

        self.assertEqual(select_rows_on_current_page.call_count, 2)
        self.assertEqual(selection["selected_count"], 97)
        self.assertEqual(selection["next_row_offset"], 50)
        self.assertEqual(selection["page_row_count"], 50)
        self.assertFalse(selection["already_at_target"])
        self.assertEqual(selection["start_page"], 1)
        self.assertEqual(selection["end_page"], 2)
        goto_next_results_page.assert_called_once()

    def test_select_batch_results_keeps_topping_up_in_strict_mode(self) -> None:
        """限定数量模式下应继续翻页直到补足目标数。"""
        checkbox_items = [FakeLocator(attributes={"name": "selectArticle"}) for _ in range(50)]
        current_pages = iter([1, 2, 3])

        with (
            patch.object(self.interactor, "_wait_for_results_ready"),
            patch.object(
                self.interactor,
                "_current_page_checkbox_items",
                side_effect=[checkbox_items, checkbox_items, checkbox_items],
            ),
            patch.object(self.interactor, "_select_rows_on_current_page", side_effect=[47, 50, 3]) as select_rows_on_current_page,
            patch.object(
                self.interactor,
                "parser",
                SimpleNamespace(parse_results_summary=lambda: {"current_page": next(current_pages)}),
            ),
            patch.object(self.interactor, "_goto_next_results_page", return_value=True) as goto_next_results_page,
            patch.object(INTERACTOR_TIME, "sleep", return_value=None),
        ):
            selection = self.interactor._select_batch_results(export_limit=100, row_offset=0, strict_target=True)

        self.assertEqual(select_rows_on_current_page.call_count, 3)
        self.assertEqual(selection["selected_count"], 100)
        self.assertEqual(selection["next_row_offset"], 3)
        self.assertEqual(selection["page_row_count"], 50)
        self.assertTrue(selection["already_at_target"])
        self.assertEqual(selection["start_page"], 1)
        self.assertEqual(selection["end_page"], 3)
        self.assertEqual(goto_next_results_page.call_count, 2)

    def test_select_batch_results_returns_reached_end_when_last_page_exhausted(self) -> None:
        """续跑游标已在末页末尾时应按正常结束返回。"""
        checkbox_items = [FakeLocator(attributes={"name": "selectArticle"}) for _ in range(50)]

        with (
            patch.object(self.interactor, "_wait_for_results_ready"),
            patch.object(self.interactor, "_current_page_checkbox_items", return_value=checkbox_items),
            patch.object(
                self.interactor,
                "parser",
                SimpleNamespace(parse_results_summary=lambda: {"current_page": 120}),
            ),
            patch.object(self.interactor, "_goto_next_results_page", return_value=False) as goto_next_results_page,
            patch.object(INTERACTOR_TIME, "sleep", return_value=None),
        ):
            selection = self.interactor._select_batch_results(export_limit=100, row_offset=50, strict_target=True)

        self.assertEqual(selection["selected_count"], 0)
        self.assertEqual(selection["next_row_offset"], 50)
        self.assertEqual(selection["page_row_count"], 50)
        self.assertFalse(selection["already_at_target"])
        self.assertTrue(selection["reached_end"])
        goto_next_results_page.assert_called_once()

    def test_current_page_checkbox_items_prefers_page_slice(self) -> None:
        all_checkbox_group = FakeCheckboxGroup(304)
        page_size_active = FakeLocator(attributes={"data-count": "50"})
        self.interactor.page = FakePage({"#selectPageSize a.active[data-count]": page_size_active})
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 2})

        with patch.object(self.interactor, "_result_checkbox_locator", return_value=all_checkbox_group):
            checkbox_items = self.interactor._current_page_checkbox_items()

        self.assertEqual(len(checkbox_items), 50)
        self.assertIs(checkbox_items[0], all_checkbox_group.items[50])
        self.assertIs(checkbox_items[-1], all_checkbox_group.items[99])

    def test_current_page_checkbox_items_skips_visible_scan_for_later_page_slice(self) -> None:
        all_checkbox_group = FakeCheckboxGroup(304)
        page_size_active = FakeLocator(attributes={"data-count": "50"})
        self.interactor.page = FakePage({"#selectPageSize a.active[data-count]": page_size_active})
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 3})

        with (
            patch.object(self.interactor, "_result_checkbox_locator", return_value=all_checkbox_group),
            patch.object(
                self.interactor,
                "_collect_visible_result_row_checkboxes",
                side_effect=AssertionError("后续页整页切片不应回退到可见性扫描"),
            ),
        ):
            checkbox_items = self.interactor._current_page_checkbox_items()

        self.assertEqual(len(checkbox_items), 50)
        self.assertIs(checkbox_items[0], all_checkbox_group.items[100])
        self.assertIs(checkbox_items[-1], all_checkbox_group.items[149])

    def test_current_page_checkbox_items_filters_select_all_from_later_page_slice(self) -> None:
        all_checkbox_group = FakeCheckboxGroup(304)
        page_size_active = FakeLocator(attributes={"data-count": "50"})
        page_three_start = 100
        select_all = all_checkbox_group.items[page_three_start]
        select_all.attributes = {"name": "selectArticleAll"}
        replacement_row = FakeLocator(attributes={"name": "selectArticle"})
        all_checkbox_group.items[page_three_start + 49] = replacement_row
        self.interactor.page = FakePage({"#selectPageSize a.active[data-count]": page_size_active})
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 3, "total_pages": 100})

        with (
            patch.object(self.interactor, "_result_checkbox_locator", return_value=all_checkbox_group),
            patch.object(
                self.interactor,
                "_collect_visible_result_row_checkboxes",
                side_effect=AssertionError("后续页单个全选框混入时不应回退到可见性扫描"),
            ),
        ):
            checkbox_items = self.interactor._current_page_checkbox_items()

        self.assertEqual(len(checkbox_items), 50)
        self.assertNotIn(select_all, checkbox_items)
        self.assertIs(checkbox_items[0], all_checkbox_group.items[101])
        self.assertIn(replacement_row, checkbox_items)
        self.assertIs(checkbox_items[-1], all_checkbox_group.items[150])

    def test_current_page_checkbox_items_falls_back_when_middle_page_slice_is_incomplete(self) -> None:
        all_checkbox_group = FakeCheckboxGroup(304)
        all_checkbox_group.items[303].attributes = {"name": "selectArticleAll"}
        visible_items = [FakeLocator(attributes={"name": "selectArticle"}) for _ in range(50)]
        page_size_active = FakeLocator(attributes={"data-count": "50"})
        self.interactor.page = FakePage({"#selectPageSize a.active[data-count]": page_size_active})
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 7, "total_pages": 100})

        with (
            patch.object(self.interactor, "_result_checkbox_locator", return_value=all_checkbox_group),
            patch.object(
                self.interactor,
                "_collect_visible_result_row_checkboxes",
                return_value=visible_items,
            ) as collect_visible_result_row_checkboxes,
        ):
            checkbox_items = self.interactor._current_page_checkbox_items()

        self.assertIs(checkbox_items, visible_items)
        collect_visible_result_row_checkboxes.assert_called_once_with(
            checkbox_locator=all_checkbox_group,
            total_count=304,
            page_size=50,
        )

    def test_current_page_checkbox_items_filters_select_all_from_broad_selector(self) -> None:
        select_all = FakeLocator(attributes={"name": "selectArticleAll"})
        row_checkboxes = [FakeLocator(attributes={"name": "selectArticle"}) for _ in range(50)]
        page_size_active = FakeLocator(attributes={"data-count": "50"})
        self.interactor.page = FakePage(
            {
                "#selectPageSize a.active[data-count]": page_size_active,
                ".search-list input[type='checkbox']": FakeLocatorGroup([select_all, *row_checkboxes]),
            }
        )
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 1})

        checkbox_items = self.interactor._current_page_checkbox_items()

        self.assertEqual(len(checkbox_items), 50)
        self.assertNotIn(select_all, checkbox_items)
        self.assertIs(checkbox_items[0], row_checkboxes[0])
        self.assertIs(checkbox_items[-1], row_checkboxes[-1])

    def test_open_export_page_retries_after_expanding_batch_menu(self) -> None:
        current_page = FakePage({})
        export_page = FakePage(
            {
                "#dateType li[data-type='excel']": FakeLocator(),
                "#dateType li[data-type='abstract']": FakeLocator(),
            },
            url="https://example.com/export",
        )
        self.interactor.page = current_page

        selector_calls: list[list[str]] = []
        visible_calls = 0

        def click_first_available(selectors: list[str], page=None) -> bool:
            del page
            selector_calls.append(selectors)
            if selectors == self.interactor.EXPORT_ENTRY_SELECTORS and len(selector_calls) == 1:
                return False
            if selectors == self.interactor.BATCH_ACTION_MENU_SELECTORS:
                return True
            combined_export_selectors = list(self.interactor.EXPORT_ENTRY_SELECTORS) + list(
                self.interactor.EXPORT_ENTRY_MENU_SELECTORS
            )
            if selectors == combined_export_selectors and len(selector_calls) == 3:
                page_context = self.interactor.page.context.pages
                page_context.append(export_page)
                return True
            return False

        def has_visible_selector(selectors: list[str], page=None) -> bool:
            del page
            nonlocal visible_calls
            combined_export_selectors = list(self.interactor.EXPORT_ENTRY_SELECTORS) + list(
                self.interactor.EXPORT_ENTRY_MENU_SELECTORS
            )
            if selectors != combined_export_selectors:
                return False
            visible_calls += 1
            return visible_calls >= 2

        with (
            patch.object(self.interactor, "_click_first_available", side_effect=click_first_available),
            patch.object(self.interactor, "_has_visible_selector", side_effect=has_visible_selector),
            patch.object(INTERACTOR_TIME, "sleep", return_value=None),
        ):
            result = self.interactor._open_export_page(timeout=1)

        self.assertIs(result, export_page)
        self.assertEqual(
            selector_calls,
            [
                self.interactor.EXPORT_ENTRY_SELECTORS,
                self.interactor.BATCH_ACTION_MENU_SELECTORS,
                list(self.interactor.EXPORT_ENTRY_SELECTORS) + list(self.interactor.EXPORT_ENTRY_MENU_SELECTORS),
            ],
        )
        self.assertEqual(visible_calls, 2)

    def test_open_export_page_retries_after_batch_dialog_export_button(self) -> None:
        current_page = FakePage({})
        export_page = FakePage(
            {
                "#dateType li[data-type='excel']": FakeLocator(),
                "#dateType li[data-type='abstract']": FakeLocator(),
            },
            url="https://example.com/export",
        )
        self.interactor.page = current_page

        selector_calls: list[list[str]] = []
        direct_visible_calls = 0
        dialog_visible_calls = 0

        def click_first_available(selectors: list[str], page=None) -> bool:
            del page
            selector_calls.append(selectors)
            if selectors == self.interactor.EXPORT_ENTRY_SELECTORS and len(selector_calls) == 1:
                return False
            if selectors == self.interactor.BATCH_ACTION_MENU_SELECTORS:
                return True
            if selectors == self.interactor.EXPORT_BATCH_DIALOG_SELECTORS:
                page_context = self.interactor.page.context.pages
                page_context.append(export_page)
                return True
            return False

        def has_visible_selector(selectors: list[str], page=None) -> bool:
            del page
            nonlocal direct_visible_calls, dialog_visible_calls
            combined_export_selectors = list(self.interactor.EXPORT_ENTRY_SELECTORS) + list(
                self.interactor.EXPORT_ENTRY_MENU_SELECTORS
            )
            if selectors == combined_export_selectors:
                direct_visible_calls += 1
                return False
            if selectors == self.interactor.EXPORT_BATCH_DIALOG_SELECTORS:
                dialog_visible_calls += 1
                return dialog_visible_calls >= 2
            return False

        with (
            patch.object(self.interactor, "_click_first_available", side_effect=click_first_available),
            patch.object(self.interactor, "_has_visible_selector", side_effect=has_visible_selector),
            patch.object(INTERACTOR_TIME, "sleep", return_value=None),
        ):
            result = self.interactor._open_export_page(timeout=1)

        self.assertIs(result, export_page)
        self.assertEqual(selector_calls[0], self.interactor.EXPORT_ENTRY_SELECTORS)
        self.assertEqual(selector_calls[1], self.interactor.BATCH_ACTION_MENU_SELECTORS)
        self.assertEqual(selector_calls[2], self.interactor.EXPORT_BATCH_DIALOG_SELECTORS)
        self.assertGreaterEqual(dialog_visible_calls, 2)

    def test_open_export_page_returns_current_page_when_same_tab_is_ready(self) -> None:
        current_page = FakePage(
            {
                "#dateType li[data-type='excel']": FakeLocator(),
                "#dateType li[data-type='abstract']": FakeLocator(),
            },
            url="https://example.com/export",
        )
        self.interactor.page = current_page

        with patch.object(self.interactor, "_click_export_entry", return_value=True):
            result = self.interactor._open_export_page(timeout=1)

        self.assertIs(result, current_page)

    def test_open_export_page_uses_page_change_timeout_by_default(self) -> None:
        config = SimpleNamespace(page_timeout=1, page_change_timeout=8)
        interactor = VpSearchInteractor(page=FakePage({}), config=config, browser_manager=None)

        with (
            patch.object(interactor, "_click_export_entry", return_value=True),
            patch.object(interactor, "_page_change_timeout_seconds", return_value=8) as page_change_timeout_seconds,
            patch.object(interactor, "_find_ready_export_page", return_value=interactor.page),
                patch.object(INTERACTOR_TIME, "time", side_effect=[100.0, 100.0]),
        ):
            result = interactor._open_export_page()

        self.assertIs(result, interactor.page)
        page_change_timeout_seconds.assert_called_once()

    def test_cleanup_export_page_goes_back_when_export_uses_current_page(self) -> None:
        current_page = FakePage({})
        self.interactor.page = current_page

        with patch.object(self.interactor, "_wait_for_results_ready") as wait_for_results_ready:
            self.interactor._cleanup_export_page(current_page)

        self.assertEqual(current_page.go_back_calls, [1000])
        self.assertEqual(current_page.close_calls, 0)
        wait_for_results_ready.assert_called_once()

    def test_cleanup_export_page_skips_restore_when_disabled(self) -> None:
        current_page = FakePage({})
        self.interactor.page = current_page

        with patch.object(self.interactor, "_wait_for_results_ready") as wait_for_results_ready:
            self.interactor._cleanup_export_page(current_page, restore_results_page=False)

        self.assertEqual(current_page.go_back_calls, [])
        self.assertEqual(current_page.close_calls, 0)
        wait_for_results_ready.assert_not_called()

    def test_export_selected_results_for_batch_uses_restore_flag(self) -> None:
        with (
            patch.object(self.interactor, "_cache_progress_page_context") as cache_progress_page_context,
            patch.object(
                self.interactor,
                "_export_selected_results",
                return_value={"excel": "metadata.xls", "txt": "reference.txt"},
            ) as export_selected_results,
        ):
            result = self.interactor._export_selected_results_for_batch(
                query="新青年",
                batch_index=1,
                output_dir=Path("F:/temp"),
                batch_selection={"already_at_target": True, "restore_results_page": False},
            )

        self.assertEqual(result, {"excel": "metadata.xls", "txt": "reference.txt"})
        cache_progress_page_context.assert_called_once()
        export_selected_results.assert_called_once_with(
            "新青年",
            1,
            Path("F:/temp"),
            already_at_target=True,
            restore_results_page=False,
        )

    def test_download_from_export_page_uses_confirm_button_when_option_has_no_auto_download(self) -> None:
        export_page = FakePage({})
        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(self.interactor, "_select_export_type") as select_export_type,
                patch.object(self.interactor, "_wait_for_export_type_selected") as wait_for_export_type_selected,
                patch.object(
                    self.interactor,
                    "_capture_export_download_by_confirm",
                    return_value=FakeDownload("vp-reference.txt"),
                ) as capture_export_download_by_confirm,
            ):
                result = self.interactor._download_from_export_page(
                    export_page=export_page,
                    selectors=["li[data-type='abstract']", "li[data-type='abstract'] a"],
                    output_dir=Path(temp_dir),
                    query="新青年",
                    batch_index=1,
                    kind="reference",
                    default_name="vp-reference.txt",
                )

        self.assertTrue(result.endswith(".txt"))
        select_export_type.assert_called_once_with(export_page=export_page, export_type="abstract", force_reclick=False)
        wait_for_export_type_selected.assert_called_once_with(export_page=export_page, export_type="abstract")
        capture_export_download_by_confirm.assert_called_once_with(export_page=export_page, kind="reference")

    def test_download_from_export_page_switches_excel_tab_then_auto_downloads(self) -> None:
        export_page = FakePage({})

        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(
                    self.interactor,
                    "_capture_export_download_by_option",
                    return_value=FakeDownload("vp-export.xls"),
                ) as capture_by_option,
                patch.object(self.interactor, "_capture_export_download_by_confirm") as capture_by_confirm,
            ):
                result = self.interactor._download_from_export_page(
                    export_page=export_page,
                    selectors=["li[data-type='excel']", "li[data-type='excel'] a"],
                    output_dir=Path(temp_dir),
                    query="新青年",
                    batch_index=1,
                    kind="metadata",
                    default_name="vp-export.xls",
                )

        self.assertTrue(result.endswith(".xls"))
        capture_by_option.assert_called_once_with(
            export_page=export_page,
            selectors=["li[data-type='excel']", "li[data-type='excel'] a"],
            kind="metadata",
        )
        capture_by_confirm.assert_not_called()

    def test_download_from_export_page_rejects_non_excel_metadata_download(self) -> None:
        export_page = FakePage({})

        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(
                    self.interactor,
                    "_capture_export_download_by_option",
                    side_effect=[FakeDownload("vp-reference.txt"), FakeDownload("vp-reference.txt")],
                ),
                patch.object(
                    self.interactor,
                    "_capture_export_download_by_confirm",
                    side_effect=[FakeDownload("vp-reference.txt"), FakeDownload("vp-reference.txt")],
                ),
            ):
                with self.assertRaises(ValidationError):
                    self.interactor._download_from_export_page(
                        export_page=export_page,
                        selectors=["li[data-type='excel']", "li[data-type='excel'] a"],
                        output_dir=Path(temp_dir),
                        query="新青年",
                        batch_index=1,
                        kind="metadata",
                        default_name="vp-export.xls",
                    )

    def test_download_from_export_page_retries_excel_after_wrong_txt_download(self) -> None:
        export_page = FakePage({})

        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(
                    self.interactor,
                    "_capture_export_download_by_option",
                    side_effect=[FakeDownload("vp-reference.txt"), FakeDownload("vp-export.xls")],
                ) as capture_by_option,
                patch.object(self.interactor, "_capture_export_download_by_confirm") as capture_by_confirm,
            ):
                result = self.interactor._download_from_export_page(
                    export_page=export_page,
                    selectors=["li[data-type='excel']", "li[data-type='excel'] a"],
                    output_dir=Path(temp_dir),
                    query="新青年",
                    batch_index=1,
                    kind="metadata",
                    default_name="vp-export.xls",
                )

        self.assertTrue(result.endswith(".xls"))
        self.assertEqual(capture_by_option.call_count, 2)
        capture_by_confirm.assert_not_called()

    def test_download_from_export_page_falls_back_to_confirm_when_excel_auto_download_not_triggered(self) -> None:
        export_page = FakePage({})

        with TemporaryDirectory() as temp_dir:
            with (
                patch.object(
                    self.interactor,
                    "_capture_export_download_by_option",
                    return_value=None,
                ) as capture_by_option,
                patch.object(self.interactor, "_select_export_type") as select_export_type,
                patch.object(self.interactor, "_wait_for_export_type_selected") as wait_for_export_type_selected,
                patch.object(
                    self.interactor,
                    "_capture_export_download_by_confirm",
                    return_value=FakeDownload("vp-export.xls"),
                ) as capture_by_confirm,
            ):
                result = self.interactor._download_from_export_page(
                    export_page=export_page,
                    selectors=["li[data-type='excel']", "li[data-type='excel'] a"],
                    output_dir=Path(temp_dir),
                    query="新青年",
                    batch_index=1,
                    kind="metadata",
                    default_name="vp-export.xls",
                )

        self.assertTrue(result.endswith(".xls"))
        capture_by_option.assert_called_once_with(
            export_page=export_page,
            selectors=["li[data-type='excel']", "li[data-type='excel'] a"],
            kind="metadata",
        )
        select_export_type.assert_called_once_with(export_page=export_page, export_type="excel", force_reclick=False)
        wait_for_export_type_selected.assert_called_once_with(export_page=export_page, export_type="excel")
        capture_by_confirm.assert_called_once_with(export_page=export_page, kind="metadata")

    def test_ensure_checkbox_checked_prefers_layui_wrapper(self) -> None:
        checkbox = FakeLocator(checked=False)

        def mark_checked(_locator: FakeLocator) -> None:
            checkbox.checked = True

        visual_checkbox = FakeLocator(
            class_name="layui-unselect layui-form-checkbox",
            on_click=mark_checked,
        )
        checkbox.child_locators = {
            "xpath=following-sibling::div[contains(@class,'layui-form-checkbox')][1]": visual_checkbox
        }

        self.interactor._ensure_checkbox_checked(checkbox, selector="input[name='selectArticleAll']")

        self.assertEqual(visual_checkbox.click_calls, [True])
        self.assertEqual(checkbox.check_calls, 0)
        self.assertTrue(checkbox.checked)


class VpInteractorResultsPageTestCase(unittest.TestCase):
    """验证结果页识别与分页显示优化逻辑。"""

    def setUp(self) -> None:
        config = SimpleNamespace(page_timeout=1, page_change_timeout=1)
        browser_manager = SimpleNamespace(is_captcha_visible=lambda page: False)
        page = FakePage({"span.selected-count": FakeLocator()})
        self.interactor = VpSearchInteractor(page=page, config=config, browser_manager=browser_manager)
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"page": "1/250"})

    def test_wait_for_results_ready_accepts_selected_count_marker(self) -> None:
        with patch.object(INTERACTOR_TIME, "sleep", return_value=None):
            self.interactor._wait_for_results_ready()

    def test_prefer_results_page_size_clicks_50_option(self) -> None:
        page_size_link = FakeLocator(class_name="")
        self.interactor.page = FakePage({"#selectPageSize a[data-count='50']": page_size_link})

        with patch.object(self.interactor, "_wait_for_results_page_size_applied") as wait_for_page_size:
            self.interactor._prefer_results_page_size()

        self.assertEqual(page_size_link.click_calls, [False])
        wait_for_page_size.assert_called_once_with(page_size_link, "1/250", 0)

    def test_prefer_results_page_size_supports_generic_data_count_selector(self) -> None:
        page_size_link = FakeLocator(class_name="")
        self.interactor.page = FakePage({"[data-count='50']": FakeLocatorGroup([page_size_link])})

        with patch.object(self.interactor, "_wait_for_results_page_size_applied") as wait_for_page_size:
            self.interactor._prefer_results_page_size()

        self.assertEqual(page_size_link.click_calls, [False])
        wait_for_page_size.assert_called_once_with(page_size_link, "1/250", 0)

    def test_extract_selected_count_reads_checked_tip_mark_number(self) -> None:
        self.interactor.page = FakePage({".checked-tip .mark-number": FakeLocator(text="20")})

        selected_count = self.interactor._extract_selected_count(0)

        self.assertEqual(selected_count, 20)

    def test_open_advanced_search_page_skips_second_goto_when_click_has_navigated(self) -> None:
        browser_manager = SimpleNamespace(
            restore_session=unittest.mock.Mock(),
            is_captcha_visible=lambda page: False,
        )
        page = FakePage({"input[name='advSearchKeywords']": FakeLocator()}, url="https://example.com/home")
        interactor = VpSearchInteractor(page=page, config=self.interactor.config, browser_manager=browser_manager)

        with patch.object(interactor, "_click_first_available", return_value=True):
            interactor._open_advanced_search_page()

        browser_manager.restore_session.assert_called_once_with()
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(page.wait_for_load_state_calls, [("domcontentloaded", 1000)])

    def test_open_advanced_search_page_raises_navigation_state_error_when_target_not_ready(self) -> None:
        browser_manager = SimpleNamespace(
            restore_session=unittest.mock.Mock(),
            is_captcha_visible=lambda page: False,
        )
        page = FakePage({}, url="https://example.com/home")
        interactor = VpSearchInteractor(page=page, config=self.interactor.config, browser_manager=browser_manager)
        interactor.config.advanced_search_url = "https://example.com/advanced-search"

        with patch.object(interactor, "_click_first_available", return_value=False):
            with self.assertRaises(NavigationStateError):
                interactor._open_advanced_search_page()

class VpInteractorYearlyExportTestCase(unittest.TestCase):
    """验证截至年份逐年导出编排。"""

    def setUp(self) -> None:
        config = SimpleNamespace(
            page_timeout=1,
            ensure_output_dir=lambda data=None: Path(tempfile.gettempdir()) / "vp-yearly-tests",
            output_dir=None,
        )
        browser_manager = SimpleNamespace(is_captcha_visible=lambda page: False)
        self.interactor = VpSearchInteractor(
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
            core_only=False,
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
            core_only=True,
        )

        self.assertEqual(
            [(item["date_from"], item["date_to"], item["core_only"]) for item in tasks],
            [("1978", "1978", True), ("1979", "1979", True), ("1980", "1980", True)],
        )

    def test_build_yearly_export_tasks_clamps_start_year_to_1949(self) -> None:
        """起始年早于 1949 时应钳制到 1949。"""
        tasks = self.interactor._build_yearly_export_tasks(
            query="新青年",
            available_years=["1980", "1979", "1978", "1949", "1915"],
            date_from="1915",
            date_to="1980",
            core_only=False,
        )

        self.assertEqual(tasks[0]["date_from"], "1949")
        self.assertEqual(tasks[0]["date_to"], "1949")
        self.assertEqual(tasks[-1]["date_from"], "1980")
        self.assertEqual(tasks[-1]["date_to"], "1980")

    def test_collect_available_end_years_reads_select_options(self) -> None:
        """可选年份应直接从结束年下拉 DOM 提取。"""
        self.interactor.page = FakePage(
            {
                "#basic_endYear option": FakeLocatorGroup(
                    [
                        FakeLocator(text="不限", attributes={"value": ""}),
                        FakeLocator(text="1978", attributes={"value": "1978"}),
                        FakeLocator(text="1979", attributes={"value": "1979"}),
                    ]
                )
            }
        )

        with patch.object(self.interactor, "_ensure_yearly_search_page_ready"):
            years = self.interactor._collect_available_end_years()

        self.assertEqual(years, ["1978", "1979"])

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
                patch.object(self.interactor, "_save_yearly_progress_snapshot"),
            ):
                result = self.interactor._run_yearly_advanced_export(
                    cli_params={
                        "query": "新青年",
                        "date_from": None,
                        "date_to": "1978",
                        "core_only": False,
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
                    "core_only": True,
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
                patch.object(self.interactor, "_save_yearly_progress_snapshot"),
            ):
                result = self.interactor._run_yearly_advanced_export(
                    cli_params={
                        "query": "新青年",
                        "date_from": None,
                        "date_to": "1979",
                        "core_only": True,
                        "max_download": None,
                    },
                    progress_file=None,
                )

        collect_years.assert_not_called()
        run_single.assert_called_once()
        self.assertEqual(result["executed_years"], ["1949", "1978", "1979"])
        self.assertEqual(result["empty_years"], ["1949"])

    def test_run_single_yearly_export_reuses_current_search_page(self) -> None:
        """年度子任务应直接复用当前高级检索页，不再重新开页。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            task = {
                "query": "新青年",
                "year": "1978",
                "date_from": "1978",
                "date_to": "1978",
                "core_only": True,
                "max_download": None,
            }
            self.interactor._is_advanced_search_page = lambda page: True

            with (
                patch.object(self.interactor, "_open_advanced_search_page") as open_page,
                patch.object(self.interactor, "_wait_for_any_selector"),
                patch.object(self.interactor, "_ensure_captcha_cleared"),
                patch.object(self.interactor, "run_advanced_export", return_value={"status": "success"}) as run_export,
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
                    "core_only": True,
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
                current_year_date_from="1979",
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
        self.assertEqual(data["runtime"]["current_year_date_from"], "1979")
        self.assertEqual(data["runtime"]["current_year_date_to"], "1979")
        self.assertTrue(data["runtime"]["current_year_progress_file"].endswith("progress.json"))


class VpInteractorPaginationTestCase(unittest.TestCase):
    """验证结果页翻页稳定性逻辑。"""

    def setUp(self) -> None:
        config = SimpleNamespace(page_timeout=1, action_timeout=1, page_change_timeout=2)
        browser_manager = SimpleNamespace(is_captcha_visible=lambda page: False)
        page = SimpleNamespace(url="https://example.com/results")
        self.interactor = VpSearchInteractor(page=page, config=config, browser_manager=browser_manager)
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"page": "1/10", "current_page": 1})

    def test_click_next_page_link_falls_back_to_js_on_last_attempt(self) -> None:
        locator = FakeLocator()
        locator.fail_click_times = 1

        self.interactor._click_next_page_link(locator, self.interactor.NEXT_PAGE_MAX_RETRIES)

        self.assertEqual(locator.scroll_calls, 1)
        self.assertEqual(locator.evaluate_calls, ["(element) => element.click()"])

    def test_goto_next_results_page_retries_after_transient_failure(self) -> None:
        locator = FakeLocator(attributes={"data-page": "2"})
        pages = iter(
            [
                {"page": "1/10", "current_page": 1},
                {"page": "1/10", "current_page": 1},
                {"page": "2/10", "current_page": 2},
                {"page": "2/10", "current_page": 2},
            ]
        )
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: next(pages))

        with (
            patch.object(self.interactor, "_find_next_page_link", return_value=locator),
            patch.object(self.interactor, "_first_result_title", return_value="标题A"),
            patch.object(self.interactor, "_click_next_page_link") as click_next_page_link,
            patch.object(
                self.interactor,
                "_wait_for_results_page_advanced",
                side_effect=[TimeoutError("首次等待超时"), None],
            ) as wait_for_results_page_advanced,
            patch.object(self.interactor, "_is_results_page_advanced", side_effect=[False, True]),
            patch.object(self.interactor, "_dismiss_confirm_dialog_if_present") as dismiss_dialog,
            patch.object(self.interactor, "_ensure_captcha_cleared") as ensure_captcha_cleared,
            patch.object(INTERACTOR_TIME, "sleep", return_value=None),
        ):
            result = self.interactor._goto_next_results_page()

        self.assertTrue(result)
        self.assertEqual(click_next_page_link.call_args_list, [call(locator, 1), call(locator, 2)])
        self.assertEqual(wait_for_results_page_advanced.call_count, 2)
        dismiss_dialog.assert_called_once()
        ensure_captcha_cleared.assert_called_once()

    def test_goto_next_results_page_rejects_title_only_state_change(self) -> None:
        locator = FakeLocator(attributes={"data-page": "2"})
        pages = iter(
            [
                {"page": "1/10", "current_page": 1},
                {"page": "1/10", "current_page": 1},
                {"page": "1/10", "current_page": 1},
            ]
        )
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: next(pages))

        with (
            patch.object(self.interactor, "_find_next_page_link", return_value=locator),
            patch.object(self.interactor, "_first_result_title", return_value="标题A"),
            patch.object(self.interactor, "_click_next_page_link"),
            patch.object(
                self.interactor,
                "_wait_for_results_page_advanced",
                side_effect=TimeoutError("等待翻页完成超时"),
            ),
            patch.object(self.interactor, "_is_results_page_advanced", return_value=False),
            patch.object(self.interactor, "_dismiss_confirm_dialog_if_present"),
            patch.object(self.interactor, "_ensure_captcha_cleared"),
            patch.object(INTERACTOR_TIME, "sleep", return_value=None),
        ):
            with self.assertRaises(TimeoutError):
                self.interactor._goto_next_results_page()

    def test_is_results_page_advanced_requires_real_page_advance(self) -> None:
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 1})
        setattr(self.interactor, "_last_results_title_before_page_turn", "标题A")

        with patch.object(self.interactor, "_first_result_title", return_value="标题B"):
            result = self.interactor._is_results_page_advanced(previous_current_page=1, target_page=2)

        self.assertFalse(result)
        self.assertEqual(getattr(self.interactor, "_known_results_page", 0), 0)

    def test_wait_for_results_page_advanced_reuses_single_summary_per_poll(self) -> None:
        summaries = iter(
            [
                {"page": "1/10", "current_page": 1},
                {"page": "2/10", "current_page": 2},
            ]
        )
        parse_call_count = 0

        def parse_results_summary():
            nonlocal parse_call_count
            parse_call_count += 1
            return next(summaries)

        self.interactor.parser = SimpleNamespace(parse_results_summary=parse_results_summary)

        with (
            patch.object(self.interactor, "_ensure_captcha_cleared"),
            patch.object(
                self.interactor,
                "_first_result_title",
                side_effect=AssertionError("页码文本已变化时不应额外读取标题"),
            ),
            patch.object(INTERACTOR_TIME, "sleep", return_value=None),
            patch.object(INTERACTOR_TIME, "time", side_effect=[0.0, 0.1, 0.2]),
        ):
            summary = self.interactor._wait_for_results_page_advanced(
                previous_current_page=1,
                target_page=2,
                previous_url="https://example.com/results",
                previous_page="1/10",
                previous_title="标题A",
                timeout=1,
            )

        self.assertEqual(summary, {"page": "2/10", "current_page": 2})
        self.assertEqual(parse_call_count, 2)

    def test_find_next_page_link_skips_disabled_header_and_uses_footer(self) -> None:
        header_next = FakeLocator(text="下一页", class_name="layui-laypage-next layui-disabled")
        footer_next = FakeLocator(text="下一页", class_name="layui-laypage-next")
        self.interactor.page = FakePage(
            {
                "#headerpager .layui-laypage-next": FakeLocatorGroup([header_next]),
                "#footerpager .layui-laypage-next": FakeLocatorGroup([footer_next]),
            }
        )

        result = self.interactor._find_next_page_link()

        self.assertIs(result, footer_next)

    def test_find_next_page_link_supports_generic_next_text(self) -> None:
        generic_next = FakeLocator(text="下一页", class_name="page-next")
        self.interactor.page = FakePage(
            {
                "#footerpager a": FakeLocatorGroup([FakeLocator(text="上一页"), generic_next]),
            }
        )

        result = self.interactor._find_next_page_link()

        self.assertIs(result, generic_next)

    def test_jump_to_results_page_uses_skip_input(self) -> None:
        skip_input = FakeLocator()
        skip_button = FakeLocator()
        self.interactor.page = FakePage(
            {
                "#footerpager .layui-laypage-skip input.layui-input": skip_input,
                "#footerpager .layui-laypage-skip .layui-laypage-btn": skip_button,
            }
        )
        pages = iter(
            [
                {"current_page": 13, "page": "13/40"},
                {"current_page": 18, "page": "18/40"},
            ]
        )
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: next(pages))

        with (
            patch.object(self.interactor, "_first_result_title", return_value="标题A"),
            patch.object(self.interactor, "_wait_for_results_changed", return_value=None),
        ):
            result = self.interactor._jump_to_results_page(target_page=18)

        self.assertTrue(result)
        self.assertEqual(skip_input.text, "18")
        self.assertEqual(skip_button.click_calls, [False])

    def test_select_dropdown_option_raises_after_timeout(self) -> None:
        config = SimpleNamespace(page_timeout=0.01)
        interactor = VpSearchInteractor(page=None, config=config, browser_manager=None)
        row = FakeLocator(count_value=1)
        row.visible_after = 999999

        with patch.object(INTERACTOR_TIME, "sleep", return_value=None):
            with self.assertRaises(ValidationError):
                interactor._select_dropdown_option(row, "OR", "或")

    def test_restore_results_position_advances_until_target_page(self) -> None:
        pages = iter(
            [
                {"current_page": 1, "page": "1/10"},
                {"current_page": 2, "page": "2/10"},
                {"current_page": 3, "page": "3/10"},
            ]
        )
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: next(pages))

        with patch.object(self.interactor, "_goto_next_results_page", return_value=True) as goto_next_results_page:
            self.interactor._restore_results_position(target_page=3)

        self.assertEqual(goto_next_results_page.call_count, 2)

    def test_restore_results_position_prefers_skip_input_before_visible_link_and_next(self) -> None:
        pages = iter(
            [
                {"current_page": 13, "page": "13/40"},
                {"current_page": 18, "page": "18/40"},
            ]
        )
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: next(pages))

        with (
            patch.object(self.interactor, "_jump_to_results_page", return_value=True) as jump_to_results_page,
            patch.object(self.interactor, "_find_resume_target_page_link") as find_resume_target_page_link,
            patch.object(self.interactor, "_goto_results_page_by_link") as goto_results_page_by_link,
            patch.object(self.interactor, "_goto_next_results_page") as goto_next_results_page,
        ):
            self.interactor._restore_results_position(target_page=18)

        jump_to_results_page.assert_called_once_with(18)
        find_resume_target_page_link.assert_not_called()
        goto_results_page_by_link.assert_not_called()
        goto_next_results_page.assert_not_called()

    def test_restore_results_position_falls_back_to_visible_link_after_skip_failure(self) -> None:
        jump_link = FakeLocator(text="17", attributes={"data-page": "17"})
        pages = iter(
            [
                {"current_page": 13, "page": "13/40"},
                {"current_page": 17, "page": "17/40"},
                {"current_page": 18, "page": "18/40"},
            ]
        )
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: next(pages))

        with (
            patch.object(self.interactor, "_jump_to_results_page", side_effect=[False, False]) as jump_to_results_page,
            patch.object(
                self.interactor,
                "_find_resume_target_page_link",
                side_effect=[jump_link, None],
            ) as find_resume_target_page_link,
            patch.object(self.interactor, "_goto_results_page_by_link", return_value=True) as goto_results_page_by_link,
            patch.object(self.interactor, "_goto_next_results_page", return_value=True) as goto_next_results_page,
        ):
            self.interactor._restore_results_position(target_page=18)

        self.assertEqual(jump_to_results_page.call_args_list, [call(18), call(18)])
        self.assertEqual(
            find_resume_target_page_link.call_args_list,
            [
                call(current_page=13, target_page=18),
                call(current_page=17, target_page=18),
            ],
        )
        goto_results_page_by_link.assert_called_once_with(jump_link)
        goto_next_results_page.assert_called_once()


class VpResumePageLinkTestCase(unittest.TestCase):
    """验证维普续跑恢复页码候选选择逻辑。"""

    def test_find_resume_target_page_link_prefers_max_visible_page(self) -> None:
        config = SimpleNamespace(page_timeout=1)
        interactor = VpSearchInteractor(page=None, config=config, browser_manager=None)
        page_15 = FakeLocator(text="15", attributes={"data-page": "15"})
        page_17 = FakeLocator(text="17", attributes={"data-page": "17"})
        next_link = FakeLocator(text="下一页", attributes={"data-page": "14"})
        interactor.page = FakePage(
            {
                "#footerpager a[data-page]": FakeLocatorGroup([page_15, page_17, next_link]),
            }
        )

        target_link = interactor._find_resume_target_page_link(current_page=13, target_page=30)

        self.assertIs(target_link, page_17)


class VpResultParserTestCase(unittest.TestCase):
    """验证维普结果页解析逻辑。"""

    def test_parse_results_summary_supports_footer_pager(self) -> None:
        page = FakePage(
            {
                "#hidShowTotalCount": FakeLocator(attributes={"value": "12500"}),
                "#footerpager": FakeLocator(),
                "#footerpager .layui-laypage-curr": FakeLocator(text="1"),
                "#footerpager .layui-laypage-last": FakeLocator(attributes={"data-page": "250"}),
                "#footerpager .layui-laypage-next": FakeLocator(class_name="layui-laypage-next", text="下一页"),
            }
        )

        summary = ResultParser(page).parse_results_summary()

        self.assertEqual(summary["page"], "1/250")
        self.assertEqual(summary["current_page"], 1)
        self.assertEqual(summary["total_pages"], 250)
        self.assertTrue(summary["has_next_page"])


class VpInteractorProgressTestCase(unittest.TestCase):
    """验证断点续跑相关逻辑。"""

    def setUp(self) -> None:
        config = SimpleNamespace(page_timeout=1)
        page = SimpleNamespace(url="https://example.com/results")
        self.interactor = VpSearchInteractor(page=page, config=config, browser_manager=None)
        self.interactor.parser = SimpleNamespace(parse_results_summary=lambda: {"current_page": 2, "page": "2/10"})

    def test_build_resume_runtime_rejects_missing_history_files(self) -> None:
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

            with self.assertRaises(ValidationError):
                self.interactor._build_resume_runtime(
                    resume_data=resume_data,
                    output_dir=Path(temp_dir),
                    planned_download=100,
                    batch_count=2,
                    total=200,
                )

    def test_save_progress_snapshot_persists_runtime_context(self) -> None:
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
                core_only=True,
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

            state = store.load()

        self.assertEqual(state["status"], "running")
        self.assertEqual(state["search_params"]["query"], "新青年")
        self.assertEqual(state["runtime"]["current_page"], 2)
        self.assertEqual(state["runtime"]["current_row_offset"], 10)
        self.assertEqual(state["runtime"]["enriched_batch_files"], [str(batch_file.resolve())])
        self.assertEqual(state["last_error"]["type"], "RuntimeError")

    def test_safe_progress_page_context_uses_cached_results_context(self) -> None:
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


class VpCliOutputTestCase(unittest.TestCase):
    """验证维普 CLI 输出与结果透出。"""

    def test_print_human_readable_shows_summary_report_path(self) -> None:
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
            "core_only": True,
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
            exit_code = VpCliMain()

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
