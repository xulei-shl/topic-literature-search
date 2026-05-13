"""Excel 去重工具 - 基于题名去重，保留每个题名的第一条记录。"""

import glob
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook, Workbook

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def normalize_title(title: str) -> str:
    """标准化题名用于去重匹配。

    处理全角/半角标点、空格差异，确保不同来源的相似题名能正确匹配。
    """
    if not title:
        return ""
    # 全角转半角（常见标点）
    ftoc = str.maketrans('，。：；？！""''（）【】《》、·—–…', ',.:;?!"\'()[]<>,.-')
    text = title.translate(ftoc)
    # 去除所有空白字符（半角空格、全角空格、制表符等）
    text = re.sub(r'\s+', '', text)
    # 转为小写便于匹配
    return text.lower()


def normalize_query_slug(query: str) -> str:
    """将查询词转换为文件名中使用的 slug（与 merge_excel.py 保持一致）。"""
    return "".join(c for c in query if c.isalnum() or c in (" ", "-", "_")).strip()[:30]


def find_latest_combined_file(query: str) -> Optional[Path]:
    """根据查询词查找最新的合并 Excel 文件。

    Args:
        query: 检索关键词。

    Returns:
        最新的合并文件路径，如果找不到则返回 None。
    """
    slug = normalize_query_slug(query)
    pattern = str(PROJECT_ROOT / "outputs" / f"*-{slug}-combined.xlsx")
    matches = glob.glob(pattern)
    if not matches:
        return None
    # 按文件名中的时间戳排序（假设时间戳在开头，格式 YYYYMMDD-HHMMSS）
    def extract_timestamp(path: Path) -> str:
        name = path.name
        # 文件名格式: 20260101-123456-xxx-combined.xlsx
        parts = name.split("-")
        if len(parts) >= 2:
            return parts[0] + parts[1]  # 合并日期和时间部分
        return ""
    matches.sort(key=lambda p: extract_timestamp(Path(p)), reverse=True)
    return Path(matches[0])


def deduplicate_by_title(input_path: Path, output_path: Optional[Path] = None) -> Path:
    """根据题名去重 Excel 文件，保留每个题名首次出现的行。

    Args:
        input_path: 输入的 Excel 文件路径。
        output_path: 输出文件路径，若为 None 则自动生成在同目录下。

    Returns:
        输出文件路径。
    """
    if output_path is None:
        stem = input_path.stem
        output_path = input_path.parent / f"{stem}_deduplicated.xlsx"

    wb_in = load_workbook(input_path, read_only=True)
    ws_in = wb_in.active

    # 读取表头
    headers = [cell.value for cell in ws_in[1]]
    if "题名" not in headers:
        raise ValueError("输入的 Excel 中缺少 '题名' 列，无法去重")

    title_col_idx = headers.index("题名")  # 0-based 索引
    source_col_idx = headers.index("来源库") if "来源库" in headers else None
    # 表头列数
    num_cols = len(headers)

    # 记录已见过的题名（使用小写+去空格以便更宽松匹配，但这里使用原始值精确匹配）
    seen_titles = set()
    rows_to_keep = []  # 存储需要保留的行数据（列表，每个元素是行值列表）

    # 遍历数据行（从第2行开始）
    for row in ws_in.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        # 过滤来源库含"外文"的行
        if source_col_idx is not None and row[source_col_idx] is not None and "外文" in str(row[source_col_idx]):
            continue
        if row[title_col_idx] is None:
            # 题名为空，可以选择保留或丢弃。此处选择保留（不去重）
            rows_to_keep.append(list(row))
            continue
        title = str(row[title_col_idx])
        normalized = normalize_title(title)
        if normalized not in seen_titles:
            seen_titles.add(normalized)
            rows_to_keep.append(list(row))

    wb_in.close()

    # 写入新文件
    wb_out = Workbook()
    ws_out = wb_out.active

    # 写入表头
    for col_idx, header in enumerate(headers, start=1):
        ws_out.cell(row=1, column=col_idx, value=header)

    # 写入保留的数据行
    for row_idx, row_data in enumerate(rows_to_keep, start=2):
        for col_idx, value in enumerate(row_data, start=1):
            ws_out.cell(row=row_idx, column=col_idx, value=value)

    wb_out.save(output_path)
    print(f"去重完成: {output_path} (原始 {ws_in.max_row - 1} 行 -> 保留 {len(rows_to_keep)} 行)")
    return output_path


def clean_excel(query: str, input_path: Optional[Path] = None) -> str:
    """对第一步合并的结果进行去重。

    Args:
        query: 检索关键词，用于自动查找合并文件。
        input_path: 可选的输入文件路径，若提供则直接使用。

    Returns:
        输出去重后的文件路径。
    """
    if input_path is None:
        combined_file = find_latest_combined_file(query)
        if combined_file is None:
            raise FileNotFoundError(f"未找到查询词 '{query}' 对应的合并文件，请先运行 merge-excel")
        input_path = combined_file
    else:
        input_path = Path(input_path)
        if not input_path.exists():
            raise FileNotFoundError(f"输入文件不存在: {input_path}")

    return str(deduplicate_by_title(input_path))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python -m src.core.clean_excel <query> [input_file]")
        sys.exit(1)
    query = sys.argv[1]
    input_file = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    clean_excel(query, input_file)