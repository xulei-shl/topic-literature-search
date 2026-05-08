"""万方页面交互测试。"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import call, patch

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
TimeoutError = _exceptions_module.TimeoutError
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
    ) -> None:
        self.text = text
        self._count_value = count_value
        self.class_name = class_name
        self.attributes = attributes or {}
        self.children = children or {}
        self.on_click = on_click
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

    def test_select_batch_results_tolerates_page_shortfall_in_full_export_mode(self) -> None:
        """全量导出模式下不应跨页补足页面缺口。"""
        row_locator = FakeLocatorGroup([FakeLocator() for _ in range(50)])
        self.interactor.page = FakePage({"div.normal-list": row_locator})

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
        row_locator = FakeLocatorGroup([FakeLocator() for _ in range(50)])
        self.interactor.page = FakePage({"div.normal-list": row_locator})

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
            patch.object(self.interactor, "_select_date_year"),
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

    def test_build_resume_runtime_rejects_missing_history_files(self) -> None:
        """历史批次文件缺失时应拒绝恢复。"""
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
