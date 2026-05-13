"""验证逐年导出结果校验工具。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from src.utils.yearly_export_validation import (
    cleanup_year_output_dir,
    count_excel_rows,
    find_latest_yearly_merged_excel,
    find_latest_yearly_summary_report,
    find_yearly_no_results_note,
    read_report_total,
)


class YearlyExportValidationTestCase(unittest.TestCase):
    """验证逐年导出结果校验工具。"""

    def test_find_latest_yearly_summary_report_skips_batch_report(self) -> None:
        """年度汇总报告选择不应误命中批次报告。"""
        with TemporaryDirectory() as temp_dir:
            year_output_dir = Path(temp_dir)
            (year_output_dir / "20260509-090000-新青年-batch001-report.txt").write_text("批次", encoding="utf-8")
            expected = year_output_dir / "20260509-100000-新青年-report.txt"
            expected.write_text("总数: 3\n", encoding="utf-8")

            result = find_latest_yearly_summary_report(year_output_dir)

        self.assertEqual(result, expected)

    def test_read_report_total_extracts_total_field(self) -> None:
        """应能从年度汇总报告中解析总数字段。"""
        with TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.txt"
            report_path.write_text("检索词: 新青年\n总数: 319\n导出: 319\n", encoding="utf-8")

            total = read_report_total(report_path)

        self.assertEqual(total, 319)

    def test_find_yearly_no_results_note_returns_note_file(self) -> None:
        """应能识别年度目录中的无结果说明文件。"""
        with TemporaryDirectory() as temp_dir:
            year_output_dir = Path(temp_dir)
            expected = year_output_dir / "1949-no-results.txt"
            expected.write_text("状态: no_results\n", encoding="utf-8")

            result = find_yearly_no_results_note(year_output_dir)

        self.assertEqual(result, expected)

    def test_count_excel_rows_reads_actual_row_count(self) -> None:
        """应能读取合并表格中的实际数据行数。"""
        with TemporaryDirectory() as temp_dir:
            excel_path = Path(temp_dir) / "20260509-100000-新青年-merged.xlsx"
            pd.DataFrame([{"题名": "甲"}, {"题名": "乙"}, {"题名": "丙"}]).to_excel(
                excel_path,
                index=False,
                engine="openpyxl",
            )

            rows = count_excel_rows(excel_path)
            latest_excel = find_latest_yearly_merged_excel(Path(temp_dir))

        self.assertEqual(rows, 3)
        self.assertEqual(latest_excel, excel_path)

    def test_cleanup_year_output_dir_removes_data_but_preserves_progress(self) -> None:
        """清理年度目录时应删除导出数据文件，但保留 progress-*.json 进度文件。"""
        with TemporaryDirectory() as temp_dir:
            year_dir = Path(temp_dir) / "year-1978"
            year_dir.mkdir()
            (year_dir / "1978-merged.xlsx").write_bytes(b"fake")
            (year_dir / "1978-report.txt").write_text("总数: 1", encoding="utf-8")
            progress_file = year_dir / "progress-新青年-f7f2b66716.json"
            progress_file.write_text("{}", encoding="utf-8")
            sub_dir = year_dir / "subdir"
            sub_dir.mkdir()
            (sub_dir / "nested.txt").write_text("nested", encoding="utf-8")

            cleanup_year_output_dir(year_dir)

            self.assertTrue(year_dir.is_dir())
            remaining = list(year_dir.iterdir())
            self.assertEqual(remaining, [progress_file])

    def test_cleanup_year_output_dir_is_safe_when_dir_missing(self) -> None:
        """年度目录不存在时不应报错。"""
        cleanup_year_output_dir(Path("/nonexistent/year-1978"))


if __name__ == "__main__":
    unittest.main()
