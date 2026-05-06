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
