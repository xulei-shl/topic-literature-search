"""CLI 输出与结果保存。"""

from pathlib import Path
from typing import Any, Dict

from src.utils.result_output import (
    build_output_slug as shared_build_output_slug,
    print_json,
    save_results as shared_save_results,
    setup_logging,
)

DEFAULT_RESULT_TYPE = "cnki"


def print_human_readable(data: Dict[str, Any]) -> None:
    """打印可读结果。"""
    result_type = data.get("result_type", "")

    if result_type == "login":
        print(data.get("message", "登录状态已保存"))
        return

    if result_type == "advanced_export":
        print(f"检索词: {data.get('query', '')}")
        print(f"状态: {data.get('status', '')}")
        if data.get("yearly_mode"):
            print("模式: 逐年导出")
        print(f"总数: {data.get('total', 0)}")
        print(f"选中: {data.get('selected', 0)}")
        print(f"导出: {data.get('exported', 0)}")
        if data.get("planned_download") is not None:
            print(f"计划导出: {data.get('planned_download', 0)}")
        if data.get("batch_count") is not None:
            print(f"批次数: {data.get('exported_batches', 0)} / {data.get('batch_count', 0)}")
        if data.get("date_range"):
            print(f"日期范围: {data.get('date_range')}")
        elif data.get("date_from") or data.get("date_to"):
            print(f"日期范围: {data.get('date_from', '')} ~ {data.get('date_to', '')}")
        print(f"核心: {'是' if data.get('core_only') else '否'}")
        print(f"URL: {data.get('url', '')}")
        if data.get("final_file_path"):
            print(f"文件: {data.get('final_file_path')}")
        if data.get("report_file"):
            print(f"报告: {data.get('report_file')}")
        if data.get("progress_file"):
            print(f"进度文件: {data.get('progress_file')}")
        if data.get("yearly_mode"):
            executed_years = data.get("executed_years") or []
            empty_years = data.get("empty_years") or []
            skipped_years = data.get("skipped_years") or []
            print(f"执行年份: {', '.join(executed_years) if executed_years else '无'}")
            print(f"无结果年份: {', '.join(empty_years) if empty_years else '无'}")
            print(f"跳过年份: {', '.join(skipped_years) if skipped_years else '无'}")
        if data.get("resumed_from_progress"):
            print("恢复模式: 是")
        return

    if result_type == "paper_detail":
        print(f"标题: {data.get('title', '')}")
        print(f"来源: {data.get('journal', '')}")
        print(f"发布时间: {data.get('pub_info', '')}")
        print(f"URL: {data.get('url', '')}")
        print(f"关键词: {', '.join(data.get('keywords', []))}")
        print(f"分类号: {data.get('classification', '')}")
        print(f"基金: {data.get('fund', '')}")
        print("作者:")
        for author in data.get("authors", []):
            name = author.get("name", "")
            affiliation_num = author.get("affiliation_num", "")
            suffix = f" [{affiliation_num}]" if affiliation_num else ""
            print(f"  - {name}{suffix}")
        if data.get("affiliations"):
            print("单位:")
            for affiliation in data["affiliations"]:
                print(f"  - {affiliation}")
        if data.get("abstract"):
            print("摘要:")
            print(data["abstract"])
        return

    results = data.get("results", [])
    print(f"检索词: {data.get('query', '')}")
    print(f"总数: {data.get('total', '0')}")
    print(f"页码: {data.get('page', '')}")
    if data.get("current_page") and data.get("total_pages"):
        print(f"当前页: {data.get('current_page')} / {data.get('total_pages')}")
    if data.get("sort_by"):
        print(f"排序: {data.get('sort_by')}")
    print(f"URL: {data.get('url', '')}")
    print("")

    if not results:
        print("当前页面没有解析到结果。")
        return

    for item in results:
        online_first = " [网络首发]" if item.get("is_online_first") else ""
        print(f"{item.get('n', 0)}. {item.get('title', '')}{online_first}")
        print(f"   作者: {'; '.join(item.get('authors', []))}")
        print(f"   来源: {item.get('journal', '')} | 日期: {item.get('date', '')} | 类型: {item.get('database', '')}")
        print(f"   被引: {item.get('citations', '')} | 下载: {item.get('downloads', '')}")
        print(f"   URL: {item.get('href', '')}")


def save_results(data: Dict[str, Any], output_dir: Path) -> str:
    """保存结果为 JSON 文件。"""
    return shared_save_results(data, output_dir, fallback_result_type=DEFAULT_RESULT_TYPE)


def build_output_slug(data: Dict[str, Any] | str, fallback: str = "cnki") -> str:
    """生成输出目录和文件名使用的 slug。"""
    return shared_build_output_slug(data, fallback)
