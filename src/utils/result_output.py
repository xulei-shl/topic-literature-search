"""检索结果输出工具。"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def setup_logging(level: str) -> None:
    """设置日志。

    Args:
        level: 日志级别字符串。
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def print_json(data: dict[str, Any]) -> None:
    """打印 JSON。

    Args:
        data: 输出数据。
    """
    print(json.dumps(data, ensure_ascii=False, indent=2))


def build_output_slug(data: dict[str, Any] | str, fallback: str) -> str:
    """生成输出目录和文件名使用的 slug。

    Args:
        data: 结果数据或原始文本。
        fallback: 兜底名称。

    Returns:
        str: slug 文本。
    """
    if isinstance(data, dict):
        text = data.get("query") or data.get("title") or data.get("result_type") or fallback
    else:
        text = data

    slug = re.sub(r"[^\w\u4e00-\u9fa5\s-]", "", str(text).lower())
    slug = re.sub(r"\s+", "-", slug).strip("-_")[:60]
    return slug or fallback


def save_results(data: dict[str, Any], output_dir: Path, fallback_result_type: str) -> str:
    """保存结果为 JSON 文件。

    Args:
        data: 结果数据。
        output_dir: 输出目录。
        fallback_result_type: `result_type` 缺失时使用的兜底名称。

    Returns:
        str: JSON 文件路径。
    """
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result_type = data.get("result_type", fallback_result_type)
    file_path = output_dir / f"{timestamp}-{build_output_slug(data, result_type)[:50]}.json"

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    return str(file_path)


def build_batch_output_filename(
    query: str,
    batch_index: int,
    kind: str,
    suffix: str,
    fallback: str = "search",
) -> str:
    """生成批次文件名。

    Args:
        query: 检索词。
        batch_index: 批次序号。
        kind: 文件类型标记。
        suffix: 文件后缀。
        fallback: slug 兜底名称。

    Returns:
        str: 文件名。
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = build_output_slug(query, fallback)[:40]
    return f"{timestamp}-{slug}-batch{batch_index:03d}-{kind}{suffix}"


def build_summary_output_filename(query: str, fallback: str = "search") -> str:
    """生成汇总文件名。

    Args:
        query: 检索词。
        fallback: slug 兜底名称。

    Returns:
        str: 汇总文件名。
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = build_output_slug(query, fallback)[:40]
    return f"{timestamp}-{slug}-merged.xlsx"


def build_export_file_path(
    output_dir: Path,
    query: str,
    batch_index: int,
    kind: str,
    suggested_name: str,
    fallback: str = "search",
    default_suffix: str = ".xls",
) -> Path:
    """生成导出文件完整路径。

    Args:
        output_dir: 输出目录。
        query: 检索词。
        batch_index: 批次序号。
        kind: 文件类型标记。
        suggested_name: 浏览器建议文件名。
        fallback: slug 兜底名称。
        default_suffix: 建议文件名缺失后缀时使用的默认后缀。

    Returns:
        Path: 文件路径。
    """
    suffix = Path(suggested_name).suffix or default_suffix
    filename = build_batch_output_filename(
        query=query,
        batch_index=batch_index,
        kind=kind,
        suffix=suffix,
        fallback=fallback,
    )
    return output_dir / filename
