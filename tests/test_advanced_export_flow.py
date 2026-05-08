"""高级检索共享骨架测试。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core.advanced_export_flow import BaseAdvancedExportFlow


class FakeProgressStore:
    """模拟进度文件存储。"""

    VERSION = 1

    def __init__(self, file_path: Path, progress_data=None) -> None:
        self.file_path = file_path
        self._progress_data = progress_data

    def exists(self) -> bool:
        return self._progress_data is not None

    def load(self):
        return self._progress_data

    def save(self, state) -> str:
        self._progress_data = state
        return str(self.file_path)

    @classmethod
    def resolve_search_params(cls, cli_params, progress_data=None):
        del progress_data
        return dict(cli_params)


class FakeExportProcessor:
    """模拟导出后处理器。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def sanitize_export_excel(self, excel_path: Path, output_path: Path) -> str:
        self.calls.append(("sanitize", str(excel_path)))
        output_path.write_text("sanitized", encoding="utf-8")
        return str(output_path)

    def enrich_batch_excel(self, excel_path: Path, txt_path: Path, output_path: Path) -> str:
        self.calls.append(("enrich", f"{excel_path}|{txt_path}"))
        output_path.write_text("enriched", encoding="utf-8")
        return str(output_path)

    def merge_batch_excels(self, batch_files: list[Path], final_file: Path) -> str:
        self.calls.append(("merge", str(len(batch_files))))
        final_file.write_text("merged", encoding="utf-8")
        return str(final_file)


class FakePage:
    """模拟页面对象。"""

    def __init__(self) -> None:
        self.url = "https://example.com/results"


class FakeParser:
    """模拟结果页解析器。"""

    def __init__(self, total: int) -> None:
        self.total = total

    def parse_results_summary(self):
        return {"total": self.total, "current_page": 1, "page": "1/1"}


class FakeBrowserManager:
    """模拟浏览器管理器。"""

    def __init__(self) -> None:
        self.saved_sessions: list[dict[str, str]] = []

    def save_session(self, **kwargs) -> None:
        self.saved_sessions.append(kwargs)


class FakeFlow(BaseAdvancedExportFlow):
    """共享骨架测试替身。"""

    ADVANCED_FORM_READY_SELECTORS = ("#ready",)

    def __init__(self, total: int, temp_dir: Path) -> None:
        self.page = FakePage()
        self.parser = FakeParser(total)
        self.browser_manager = FakeBrowserManager()
        self.export_processor = FakeExportProcessor()
        self.temp_dir = temp_dir
        self.progress_snapshots: list[dict] = []
        self.selected_batches: list[int] = []
        self.strict_target_calls: list[bool] = []
        self.raise_on_export = False
        self.resume_runtime_override = None

    def _prepare_progress_store(self, progress_file, cli_params):
        del cli_params
        return FakeProgressStore(progress_file or self.temp_dir / "progress.json"), None

    def _resolve_output_dir(self, query: str, resume_data):
        del query, resume_data
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        return self.temp_dir

    def _build_resume_runtime(self, resume_data, output_dir: Path, planned_download: int, batch_count: int, total: int):
        del resume_data, output_dir, planned_download, batch_count, total
        if self.resume_runtime_override is not None:
            return dict(self.resume_runtime_override)
        return {
            "exported_total": 0,
            "exported_batches": 0,
            "next_batch_index": 1,
            "current_page": 1,
            "current_row_offset": 0,
            "enriched_batch_files": [],
        }

    def _save_progress_snapshot_for_flow(
        self,
        progress_store,
        status: str,
        search_params,
        output_dir: Path,
        planned_download: int,
        batch_count: int,
        exported_total: int,
        exported_batches: int,
        next_batch_index: int,
        current_page: int,
        current_row_offset: int,
        enriched_batch_files: list[Path],
        final_file_path: str,
        error=None,
    ) -> None:
        del progress_store, output_dir
        self.progress_snapshots.append(
            {
                "status": status,
                "query": search_params["query"],
                "planned_download": planned_download,
                "batch_count": batch_count,
                "exported_total": exported_total,
                "exported_batches": exported_batches,
                "next_batch_index": next_batch_index,
                "current_page": current_page,
                "current_row_offset": current_row_offset,
                "enriched_batch_files": list(enriched_batch_files),
                "final_file_path": final_file_path,
                "error": type(error).__name__ if error else "",
            }
        )

    def _open_advanced_search_page(self) -> None:
        return None

    def _fill_advanced_search_form_from_params(self, search_params) -> None:
        self.last_search_params = dict(search_params)

    def _submit_advanced_search(self) -> None:
        return None

    def _restore_results_position(self, target_page: int) -> None:
        self.restored_page = target_page

    def _clear_selected_results(self) -> None:
        return None

    def _select_batch_results(self, export_limit: int, row_offset: int, strict_target: bool):
        self.selected_batches.append(export_limit)
        self.strict_target_calls.append(strict_target)
        return {
            "selected_count": export_limit,
            "next_row_offset": row_offset + export_limit,
            "page_row_count": export_limit,
            "start_page": 1,
            "end_page": 1,
        }

    def _export_selected_results_for_batch(self, query: str, batch_index: int, output_dir: Path, batch_selection):
        del batch_selection
        if self.raise_on_export:
            raise RuntimeError("导出失败")
        excel_path = output_dir / f"{query}-{batch_index}.xls"
        txt_path = output_dir / f"{query}-{batch_index}.txt"
        excel_path.write_text("excel", encoding="utf-8")
        txt_path.write_text("txt", encoding="utf-8")
        return {"excel": str(excel_path), "txt": str(txt_path)}

    def _wait_for_any_selector(self, selectors: list[str], timeout=None) -> None:
        del selectors, timeout
        return None

    def _wait_for_results_ready(self) -> None:
        return None

    def _ensure_captcha_cleared(self) -> None:
        return None

    def _format_date_range(self, date_from, date_to) -> str:
        if date_from and date_to:
            return f"{date_from} ~ {date_to}"
        return date_from or date_to or ""

    def _normalize_download_limit(self, num_results, total_results: int) -> int:
        if num_results is None:
            return total_results
        return min(int(num_results), total_results)


class AdvancedExportFlowTestCase(unittest.TestCase):
    """验证共享骨架主流程。"""

    def test_no_results_returns_no_results_payload(self) -> None:
        with TemporaryDirectory() as temp_dir:
            flow = FakeFlow(total=0, temp_dir=Path(temp_dir))

            result = flow.run_advanced_export(
                cli_params={
                    "query": "测试主题",
                    "date_from": "2020",
                    "date_to": "2025",
                    "core_only": True,
                    "max_download": None,
                }
            )

        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["total"], 0)
        self.assertEqual(flow.progress_snapshots[-1]["status"], "no_results")

    def test_success_flow_merges_batches_and_updates_cursor(self) -> None:
        with TemporaryDirectory() as temp_dir:
            flow = FakeFlow(total=3, temp_dir=Path(temp_dir))

            result = flow.run_advanced_export(
                cli_params={
                    "query": "测试主题",
                    "date_from": "2020",
                    "date_to": "2025",
                    "core_only": False,
                    "max_download": 2,
                }
            )

            self.assertTrue(Path(result["report_file"]).exists())
            self.assertTrue(Path(result["batch_report_files"][0]).exists())
            batch_report_text = Path(result["batch_report_files"][0]).read_text(encoding="utf-8")
            self.assertIn("页码范围: 1", batch_report_text)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["exported"], 2)
        self.assertTrue(result["final_file_path"].endswith(".xlsx"))
        self.assertTrue(result["report_file"].endswith(".txt"))
        self.assertEqual(len(result["batch_report_files"]), 1)
        self.assertEqual(flow.selected_batches, [2])
        self.assertEqual(flow.strict_target_calls, [True])
        self.assertEqual(flow.progress_snapshots[-1]["status"], "success")
        self.assertEqual(flow.progress_snapshots[-1]["next_batch_index"], 2)

    def test_full_export_uses_non_strict_batch_target(self) -> None:
        """全量导出模式应按页窗口推进，而不是强制补足批次目标。"""
        with TemporaryDirectory() as temp_dir:
            flow = FakeFlow(total=3, temp_dir=Path(temp_dir))

            result = flow.run_advanced_export(
                cli_params={
                    "query": "测试主题",
                    "date_from": None,
                    "date_to": None,
                    "core_only": False,
                    "max_download": None,
                }
            )

            self.assertTrue(Path(result["report_file"]).exists())

        self.assertEqual(result["status"], "success")
        self.assertEqual(flow.strict_target_calls, [False])

    def test_exception_flow_writes_failed_snapshot(self) -> None:
        with TemporaryDirectory() as temp_dir:
            flow = FakeFlow(total=2, temp_dir=Path(temp_dir))
            flow.raise_on_export = True

            with self.assertRaises(RuntimeError):
                flow.run_advanced_export(
                    cli_params={
                        "query": "测试主题",
                        "date_from": None,
                        "date_to": None,
                        "core_only": False,
                        "max_download": 1,
                    }
                )

        self.assertEqual(flow.progress_snapshots[-1]["status"], "failed")
        self.assertEqual(flow.progress_snapshots[-1]["error"], "RuntimeError")

    def test_exception_flow_keeps_last_committed_resume_cursor(self) -> None:
        """批次失败时应保留上一次成功提交后的恢复游标。"""
        with TemporaryDirectory() as temp_dir:
            flow = FakeFlow(total=7625, temp_dir=Path(temp_dir))
            flow.raise_on_export = True
            flow.resume_runtime_override = {
                "exported_total": 1000,
                "exported_batches": 2,
                "next_batch_index": 3,
                "current_page": 21,
                "current_row_offset": 0,
                "enriched_batch_files": [],
            }

            with self.assertRaises(RuntimeError):
                flow.run_advanced_export(
                    cli_params={
                        "query": "测试主题",
                        "date_from": None,
                        "date_to": None,
                        "core_only": False,
                        "max_download": None,
                    }
                )

        self.assertEqual(flow.progress_snapshots[-1]["status"], "failed")
        self.assertEqual(flow.progress_snapshots[-1]["current_page"], 21)
        self.assertEqual(flow.progress_snapshots[-1]["current_row_offset"], 0)


if __name__ == "__main__":
    unittest.main()
