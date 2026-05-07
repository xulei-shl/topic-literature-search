"""CLI 输出与结果保存。"""

from pathlib import Path
from typing import Any, Dict

from src.utils.result_output import (
    build_output_slug as shared_build_output_slug,
    print_json,
    save_results as shared_save_results,
    setup_logging,
)

DEFAULT_RESULT_TYPE = "vp"


def print_human_readable(data: Dict[str, Any]) -> None:
    """打印可读结果。

    Args:
        data: 输出数据。
    """
    result_type = data.get("result_type", "")

    if result_type == "login":
        print(data.get("message", "登录状态已保存"))
        return

    if result_type == "advanced_export":
        print(f"检索词: {data.get('query', '')}")
        print(f"状态: {data.get('status', '')}")
        print(f"总数: {data.get('total', 0)}")
        print(f"选中: {data.get('selected', 0)}")
        print(f"导出: {data.get('exported', 0)}")
        print(f"计划导出: {data.get('planned_download', 0)}")
        print(f"批次数: {data.get('exported_batches', 0)} / {data.get('batch_count', 0)}")
        if data.get("date_range"):
            print(f"日期范围: {data.get('date_range')}")
        print(f"核心: {'是' if data.get('core_only') else '否'}")
        print(f"URL: {data.get('url', '')}")
        if data.get("final_file_path"):
            print(f"文件: {data.get('final_file_path')}")
        if data.get("report_file"):
            print(f"报告: {data.get('report_file')}")
        if data.get("progress_file"):
            print(f"进度文件: {data.get('progress_file')}")
        if data.get("resumed_from_progress"):
            print("恢复模式: 是")
        return

    print_json(data)


def save_results(data: Dict[str, Any], output_dir: Path) -> str:
    """保存结果为 JSON 文件。

    Args:
        data: 结果数据。
        output_dir: 输出目录。

    Returns:
        str: JSON 文件路径。
    """
    return shared_save_results(data, output_dir, fallback_result_type=DEFAULT_RESULT_TYPE)


def build_output_slug(data: Dict[str, Any] | str, fallback: str = "vp") -> str:
    """生成输出目录和文件名使用的 slug。

    Args:
        data: 结果数据或原始文本。
        fallback: 兜底名称。

    Returns:
        str: slug 文本。
    """
    return shared_build_output_slug(data, fallback)
