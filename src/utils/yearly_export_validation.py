"""逐年导出结果校验工具。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

import pandas as pd


REPORT_TOTAL_PATTERN = re.compile(r"^总数:\s*(\d+)\s*$", re.MULTILINE)


def find_latest_yearly_summary_report(year_output_dir: Path) -> Optional[Path]:
    """返回年度目录中最新的汇总报告文件。"""
    candidates = [
        path
        for path in year_output_dir.glob("*-report.txt")
        if "-batch" not in path.name and not path.name.endswith("-no-results.txt")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.name)


def find_latest_yearly_merged_excel(year_output_dir: Path) -> Optional[Path]:
    """返回年度目录中最新的合并结果文件。"""
    candidates = list(year_output_dir.glob("*-merged.xlsx"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.name)


def find_yearly_no_results_note(year_output_dir: Path) -> Optional[Path]:
    """返回年度目录中的无结果说明文件。"""
    candidates = list(year_output_dir.glob("*-no-results.txt"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.name)


def read_report_total(report_path: Path) -> int:
    """从汇总报告中读取总数字段。"""
    content = report_path.read_text(encoding="utf-8")
    match = REPORT_TOTAL_PATTERN.search(content)
    if match is None:
        raise ValueError(f"未在报告中找到总数字段: {report_path}")
    return int(match.group(1))


def count_excel_rows(excel_path: Path) -> int:
    """统计导出 Excel 的实际数据行数。"""
    dataframe = pd.read_excel(excel_path, engine="openpyxl")
    return len(dataframe.index)


def cleanup_year_output_dir(year_output_dir: Path) -> None:
    """删除年度输出目录下的导出数据文件，保留进度文件以支持续传。"""
    if not year_output_dir.is_dir():
        return
    for item in year_output_dir.iterdir():
        if item.name.startswith("progress-") and item.name.endswith(".json"):
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        except OSError:
            pass
