"""检索 CLI 日期参数工具。"""

import re
from typing import Optional


def parse_cli_date(raw_value: str, boundary: str) -> str:
    """解析 CLI 日期年份参数。

    Args:
        raw_value: 原始日期参数。
        boundary: 日期边界类型，只允许 `start` 或 `end`。

    Returns:
        str: 归一化后的年份字符串。

    Raises:
        ValueError: 输入为空、格式非法或边界类型不支持时抛出。
    """
    value = raw_value.strip()
    if not value:
        raise ValueError("日期不能为空")

    if boundary not in ("start", "end"):
        raise ValueError(f"不支持的日期边界: {boundary}")

    if not re.fullmatch(r"\d{4}", value):
        raise ValueError(f"无效的日期: {raw_value}")

    year = int(value)
    if year <= 0:
        raise ValueError(f"无效的日期: {raw_value}")

    return f"{year:04d}"


def normalize_date_range(date_from_raw: Optional[str], date_to_raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """归一化高级检索日期范围。

    Args:
        date_from_raw: 起始年份原始值。
        date_to_raw: 结束年份原始值。

    Returns:
        tuple[Optional[str], Optional[str]]: 归一化后的起止年份。

    Raises:
        ValueError: 起始年份大于结束年份时抛出。
    """
    date_from = parse_cli_date(date_from_raw, "start") if date_from_raw else None
    date_to = parse_cli_date(date_to_raw, "end") if date_to_raw else None

    if date_from and date_to and date_from > date_to:
        raise ValueError(f"起始日期不能大于结束日期: {date_from_raw} > {date_to_raw}")
    return date_from, date_to
