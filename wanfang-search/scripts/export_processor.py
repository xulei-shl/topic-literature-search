"""万方导出结果本地处理。"""

from io import StringIO
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
from win32com.client import DispatchEx

from exceptions import ExportProcessingError

logger = logging.getLogger("wanfang_search.export_processor")

REFERENCE_COLUMN = "参考格式"
HEADER_KEYWORDS = (
    "序号",
    "题名",
    "作者",
    "作者单位",
    "刊名",
    "ISSN",
    "页码",
    "摘要",
    "关键词",
    "DOI",
    "CN",
    "核心类型",
    "中图分类号",
    "授予学位",
    "学位授予单位",
    "导师",
    "学位年度",
)


class ExportResultProcessor:
    """负责处理万方导出的表格与引文文本。"""

    def sanitize_export_excel(self, excel_path: Path, output_path: Path) -> str:
        """清理万方导出表格并重新保存。"""
        raw_dataframe = self._read_export_table(excel_path)
        sanitized = self._sanitize_export_dataframe(raw_dataframe)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sanitized.to_excel(output_path, index=False, engine="openpyxl")
        logger.debug(
            "XLS 清理: 原始行数=%s, 清理后行数=%s, 删除首行=%s",
            len(raw_dataframe.index),
            len(sanitized.index),
            self._looks_like_type_label_row(self._first_non_empty_row(raw_dataframe)),
        )
        return str(output_path)

    def parse_reference_txt(self, txt_path: Path) -> list[str]:
        """解析参考文献 TXT。"""
        content = txt_path.read_text(encoding="utf-8").strip()
        if not content:
            raise ExportProcessingError(f"参考格式文件为空: {txt_path}")

        matches = list(re.finditer(r"(?m)^\[(\d+)\]", content))
        if not matches:
            raise ExportProcessingError(f"未识别到参考格式编号: {txt_path}")

        references: list[str] = []
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            references.append(re.sub(r"\s*\n\s*", " ", content[start:end].strip()))
        return references

    def enrich_batch_excel(self, excel_path: Path, txt_path: Path, output_path: Path) -> str:
        """为批次 Excel 回填参考格式列。"""
        dataframe = self._read_clean_excel(excel_path)
        references = self.parse_reference_txt(txt_path)
        if len(dataframe.index) != len(references):
            raise ExportProcessingError(
                f"参考格式数量与表格行数不一致: rows={len(dataframe.index)}, refs={len(references)}, file={excel_path}"
            )

        enriched = dataframe.copy()
        enriched[REFERENCE_COLUMN] = references
        output_path.parent.mkdir(parents=True, exist_ok=True)
        enriched.to_excel(output_path, index=False, engine="openpyxl")
        return str(output_path)

    def merge_batch_excels(self, excel_paths: list[Path], output_path: Path) -> str:
        """合并多个批次 Excel。"""
        if not excel_paths:
            raise ExportProcessingError("没有可合并的批次文件")

        frames = [pd.read_excel(path, engine="openpyxl") for path in excel_paths]
        merged = pd.concat(frames, ignore_index=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_excel(output_path, index=False, engine="openpyxl")
        return str(output_path)

    def _read_export_table(self, excel_path: Path) -> pd.DataFrame:
        suffix = excel_path.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            return self._read_raw_excel_with_openpyxl(excel_path)
        if suffix == ".xls":
            return self._read_legacy_excel(excel_path)
        raise ExportProcessingError(f"不支持的表格格式: {excel_path}")

    def _read_clean_excel(self, excel_path: Path) -> pd.DataFrame:
        try:
            dataframe = pd.read_excel(excel_path, engine="openpyxl").fillna("")
        except Exception as exc:
            raise ExportProcessingError(f"读取 xlsx 失败: {excel_path}") from exc
        return self._normalize_dataframe(dataframe)

    def _read_raw_excel_with_openpyxl(self, excel_path: Path) -> pd.DataFrame:
        try:
            dataframe = pd.read_excel(excel_path, engine="openpyxl", header=None).fillna("")
        except Exception as exc:
            raise ExportProcessingError(f"读取 xlsx 失败: {excel_path}") from exc
        return self._normalize_dataframe(dataframe)

    def _read_legacy_excel(self, excel_path: Path) -> pd.DataFrame:
        readers = [self._read_html_table, self._read_excel_via_com]
        errors: list[str] = []
        for reader in readers:
            try:
                return reader(excel_path).fillna("")
            except Exception as exc:
                errors.append(f"{reader.__name__}: {exc}")
        raise ExportProcessingError(f"读取 xls 失败: {excel_path}; {' | '.join(errors)}")

    def _read_html_table(self, excel_path: Path) -> pd.DataFrame:
        try:
            html_content = excel_path.read_text(encoding="utf-8")
            tables = pd.read_html(StringIO(html_content), flavor="lxml", header=None)
        except ValueError as exc:
            raise ExportProcessingError(f"未识别到 HTML 表格: {excel_path}") from exc
        if not tables:
            raise ExportProcessingError(f"HTML 表格为空: {excel_path}")
        return self._normalize_dataframe(tables[0])

    def _sanitize_export_dataframe(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        if dataframe.empty:
            return dataframe

        rows = self._iter_normalized_rows(dataframe)
        rows = [row for row in rows if not self._is_empty_row(row)]
        if not rows:
            return pd.DataFrame()

        removed_first_row = False
        if self._looks_like_type_label_row(rows[0]) and len(rows) > 1:
            rows = rows[1:]
            removed_first_row = True

        current_header: list[str] = []
        current_header_start = 0
        all_columns: list[str] = []
        records: list[dict[str, Any]] = []

        for row_values in rows:
            if self._looks_like_type_label_row(row_values):
                continue

            if self._is_header_row(row_values):
                current_header_start = self._first_non_empty_index(row_values)
                current_header = self._normalize_header(row_values[current_header_start:])
                all_columns = self._merge_columns(all_columns, current_header)
                continue

            if not current_header:
                continue

            trimmed_row = self._slice_row(row_values, current_header_start, len(current_header))
            if self._is_empty_row(trimmed_row):
                continue

            record = self._build_record(current_header, trimmed_row)
            if self._is_empty_record(record):
                continue
            records.append(record)

        if not all_columns:
            raise ExportProcessingError("未识别到万方导出表头")

        sanitized = pd.DataFrame(records)
        if sanitized.empty:
            sanitized = pd.DataFrame(columns=all_columns)
        else:
            for column in all_columns:
                if column not in sanitized.columns:
                    sanitized[column] = ""
            sanitized = sanitized.reindex(columns=all_columns).fillna("")

        logger.debug(
            "XLS 清理: 原始行数=%s, 清理后行数=%s, 删除首行=%s",
            len(dataframe.index),
            len(sanitized.index),
            removed_first_row,
        )
        return sanitized

    def _is_header_row(self, row_values: list[str]) -> bool:
        """判断当前行是否为万方导出的分段表头。"""
        normalized = [self._normalize_cell(value) for value in row_values if self._normalize_cell(value)]
        if "题名" not in normalized:
            return False
        matched_keywords = [value for value in normalized if value in HEADER_KEYWORDS]
        return len(matched_keywords) >= 2

    def _normalize_header(self, header_values: list[str]) -> list[str]:
        return [self._normalize_cell(value) for value in header_values if self._normalize_cell(value)]

    def _slice_row(self, row_values: list[str], start_index: int, expected_length: int) -> list[str]:
        sliced = row_values[start_index:] if start_index < len(row_values) else []
        if len(sliced) < expected_length:
            sliced = sliced + [""] * (expected_length - len(sliced))
        return sliced[:expected_length]

    def _looks_like_type_label_row(self, row_values: list[str]) -> bool:
        non_empty_values = [value for value in row_values if value]
        if len(non_empty_values) != 1:
            return False
        return non_empty_values[0] not in HEADER_KEYWORDS

    def _first_non_empty_index(self, row_values: list[str]) -> int:
        for index, value in enumerate(row_values):
            if value:
                return index
        return 0

    def _first_non_empty_row(self, dataframe: pd.DataFrame) -> list[str]:
        for row_values in self._iter_normalized_rows(dataframe):
            if not self._is_empty_row(row_values):
                return row_values
        return []

    def _iter_normalized_rows(self, dataframe: pd.DataFrame) -> list[list[str]]:
        rows: list[list[str]] = []
        for row in dataframe.itertuples(index=False, name=None):
            rows.append(self._trim_trailing_empty_cells([self._normalize_cell(value) for value in row]))
        return rows

    def _trim_trailing_empty_cells(self, row_values: list[Any]) -> list[str]:
        values = [str(value).strip() if value is not None else "" for value in row_values]
        while values and not values[-1]:
            values.pop()
        return values

    def _is_empty_row(self, row_values: list[str]) -> bool:
        return not any(str(value).strip() for value in row_values)

    def _is_empty_record(self, record: dict[str, Any]) -> bool:
        return not any(str(value).strip() for value in record.values())

    def _build_record(self, headers: list[str], row_values: list[str]) -> dict[str, Any]:
        """按当前分段表头构建一条统一记录。"""
        record: dict[str, Any] = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            record[header] = row_values[index] if index < len(row_values) else ""
        return record

    def _merge_columns(self, existing_columns: list[str], current_headers: list[str]) -> list[str]:
        """按首次出现顺序维护统一列集合。"""
        merged = existing_columns.copy()
        for header in current_headers:
            if not header or header in merged:
                continue
            merged.append(header)
        return merged

    def _read_excel_via_com(self, excel_path: Path) -> pd.DataFrame:
        excel = None
        workbook = None
        temp_path = excel_path.with_name(f"{excel_path.stem}_tmp.xlsx")
        try:
            excel = DispatchEx("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            workbook = excel.Workbooks.Open(str(excel_path.resolve()))
            workbook.SaveAs(str(temp_path.resolve()), FileFormat=51)
        except Exception as exc:
            raise ExportProcessingError(f"Excel COM 转存失败: {excel_path}") from exc
        finally:
            if workbook is not None:
                workbook.Close(False)
            if excel is not None:
                excel.Quit()

        try:
            return self._read_raw_excel_with_openpyxl(temp_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _normalize_dataframe(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        normalized = dataframe.copy()
        normalized.columns = [self._normalize_cell(column) for column in normalized.columns]
        if normalized.empty:
            return normalized
        normalized = normalized.map(self._normalize_cell)
        normalized = normalized.loc[:, ~(normalized.eq("").all())]
        return normalized.map(self._normalize_cell)

    def _normalize_cell(self, value: Any) -> Any:
        if pd.isna(value):
            return ""
        if isinstance(value, str):
            return re.sub(r"\s+", " ", value).strip()
        return value
