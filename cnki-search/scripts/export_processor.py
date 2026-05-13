"""CNKI 导出结果本地处理。"""

from io import StringIO
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
from win32com.client import DispatchEx

from exceptions import ExportProcessingError

logger = logging.getLogger("cnki_search.export_processor")

REFERENCE_COLUMN = "参考格式"
HEADER_ROW_PREFIXES = ("SrcDatabase-", "Title-", "Author-")


class ExportResultProcessor:
    """负责处理 CNKI 导出的表格与引文文本。"""

    def sanitize_export_excel(self, excel_path: Path, output_path: Path) -> str:
        """清理导出表格中的重复表头并重新保存。

        Args:
            excel_path: 原始导出表格路径。
            output_path: 清理后表格输出路径。

        Returns:
            str: 清理后表格路径字符串。
        """
        dataframe = self._sanitize_export_dataframe(self._read_export_table(excel_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        dataframe.to_excel(output_path, index=False, engine="openpyxl")
        logger.info("导出表格已清理并完成统一列映射", extra={"excel_path": str(excel_path), "output_path": str(output_path)})
        return str(output_path)

    def parse_reference_txt(self, txt_path: Path) -> list[str]:
        """解析 GB/T 引文文本。

        Args:
            txt_path: TXT 文件路径。

        Returns:
            list[str]: 按顺序排列的参考格式列表。

        Raises:
            ExportProcessingError: TXT 内容为空或格式异常时抛出。
        """
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
            item = content[start:end].strip()
            references.append(re.sub(r"\s*\n\s*", " ", item))
        return references

    def enrich_batch_excel(
        self,
        excel_path: Path,
        txt_path: Path,
        output_path: Path,
    ) -> str:
        """为批次 Excel 回填参考格式列。

        Args:
            excel_path: 原始 Excel 路径。
            txt_path: 引文 TXT 路径。
            output_path: 增强后 xlsx 输出路径。

        Returns:
            str: 输出文件路径字符串。

        Raises:
            ExportProcessingError: 行数不匹配或表格无法读取时抛出。
        """
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
        logger.info("批次文件已回填参考格式", extra={"excel_path": str(excel_path), "output_path": str(output_path)})
        return str(output_path)

    def merge_batch_excels(
        self, excel_paths: list[Path], output_path: Path, check_reference_column: bool = False
    ) -> str:
        """合并多个批次 Excel。

        Args:
            excel_paths: 批次 xlsx 路径列表。
            output_path: 最终汇总 xlsx 路径。
            check_reference_column: 是否检查每个文件是否包含参考格式列。
        """
        if not excel_paths:
            raise ExportProcessingError("没有可合并的批次文件")

        if check_reference_column:
            for path in excel_paths:
                df = pd.read_excel(path, engine="openpyxl")
                if REFERENCE_COLUMN not in df.columns:
                    raise ExportProcessingError(f"文件缺少参考格式列: {path.name}")

        frames = [pd.read_excel(path, engine="openpyxl") for path in excel_paths]
        merged = pd.concat(frames, ignore_index=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_excel(output_path, index=False, engine="openpyxl")
        return str(output_path)

    def _read_export_table(self, excel_path: Path) -> pd.DataFrame:
        """读取 CNKI 导出表格。

        Args:
            excel_path: 导出表格路径。

        Returns:
            pd.DataFrame: 表格数据。

        Raises:
            ExportProcessingError: 无法读取表格时抛出。
        """
        suffix = excel_path.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            return self._read_raw_excel_with_openpyxl(excel_path)

        if suffix == ".xls":
            return self._read_legacy_excel(excel_path)

        raise ExportProcessingError(f"不支持的表格格式: {excel_path}")

    def _read_clean_excel(self, excel_path: Path) -> pd.DataFrame:
        """读取清理后的 xlsx 表格。"""
        try:
            dataframe = pd.read_excel(excel_path, engine="openpyxl").fillna("")
        except Exception as exc:
            raise ExportProcessingError(f"读取 xlsx 失败: {excel_path}") from exc
        return self._normalize_dataframe(dataframe)

    def _read_raw_excel_with_openpyxl(self, excel_path: Path) -> pd.DataFrame:
        """按原始二维数据读取 xlsx 表格。"""
        try:
            dataframe = pd.read_excel(excel_path, engine="openpyxl", header=None).fillna("")
        except Exception as exc:
            raise ExportProcessingError(f"读取 xlsx 失败: {excel_path}") from exc
        return self._normalize_dataframe(dataframe)

    def _read_legacy_excel(self, excel_path: Path) -> pd.DataFrame:
        """读取旧版 xls 或伪 xls 表格。"""
        readers = [self._read_html_table, self._read_excel_via_com]
        errors: list[str] = []
        for reader in readers:
            try:
                return reader(excel_path).fillna("")
            except Exception as exc:
                errors.append(f"{reader.__name__}: {exc}")

        raise ExportProcessingError(f"读取 xls 失败: {excel_path}; {' | '.join(errors)}")

    def _read_html_table(self, excel_path: Path) -> pd.DataFrame:
        """读取 HTML 伪装的 xls。"""
        try:
            html_content = excel_path.read_text(encoding="utf-8")
            tables = pd.read_html(StringIO(html_content), flavor="lxml", header=None)
        except ValueError as exc:
            raise ExportProcessingError(f"未识别到 HTML 表格: {excel_path}") from exc

        if not tables:
            raise ExportProcessingError(f"HTML 表格为空: {excel_path}")
        return self._normalize_dataframe(tables[0])

    def _sanitize_export_dataframe(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """按分段表头将原始导出表格规整为单一主表。"""
        if dataframe.empty:
            return dataframe

        current_headers = self._infer_initial_headers(dataframe)
        all_columns = self._merge_columns([], current_headers)
        records: list[dict[str, Any]] = []

        for row_values in self._iter_normalized_rows(dataframe):
            if self._is_empty_row(row_values):
                continue

            if self._is_header_row(row_values):
                current_headers = self._normalize_header_row(row_values)
                all_columns = self._merge_columns(all_columns, current_headers)
                continue

            if not current_headers:
                continue

            record = self._build_record(current_headers, row_values)
            if not record or self._is_empty_record(record):
                continue
            records.append(record)

        if not all_columns:
            return pd.DataFrame()

        sanitized = pd.DataFrame(records)
        for column in all_columns:
            if column not in sanitized.columns:
                sanitized[column] = ""
        sanitized = sanitized.reindex(columns=all_columns).fillna("")
        return self._normalize_dataframe(sanitized)

    def _infer_initial_headers(self, dataframe: pd.DataFrame) -> list[str]:
        """兼容 read_html 已将首个表头提升为列名的场景。"""
        normalized_columns = [self._normalize_cell(column) for column in dataframe.columns.tolist()]
        if not normalized_columns:
            return []
        if all(self._is_numeric_like(column) for column in normalized_columns):
            return []
        return [str(column).strip() for column in normalized_columns]

    def _is_header_row(self, row_values: list[str]) -> bool:
        """判断一行是否为新的分段表头。"""
        return all(
            any(cell.startswith(prefix) for cell in row_values if cell)
            for prefix in HEADER_ROW_PREFIXES
        )

    def _normalize_header_row(self, row_values: list[str]) -> list[str]:
        """规范化分段表头。"""
        return [self._normalize_cell(value) for value in row_values]

    def _build_record(self, headers: list[str], row_values: list[str]) -> dict[str, Any]:
        """将一行数据按当前表头映射为记录。"""
        record: dict[str, Any] = {}
        for index, header in enumerate(headers):
            header_name = str(header).strip()
            if not header_name:
                continue
            record[header_name] = row_values[index] if index < len(row_values) else ""
        return record

    def _merge_columns(self, existing_columns: list[str], current_headers: list[str]) -> list[str]:
        """按首次出现顺序维护统一列集合。"""
        merged = existing_columns.copy()
        for header in current_headers:
            header_name = str(header).strip()
            if not header_name or header_name in merged:
                continue
            merged.append(header_name)
        return merged

    def _iter_normalized_rows(self, dataframe: pd.DataFrame) -> list[list[str]]:
        """返回规范化后的原始行数据。"""
        rows: list[list[str]] = []
        for row in dataframe.itertuples(index=False, name=None):
            rows.append(self._trim_trailing_empty_cells([self._normalize_cell(value) for value in row]))
        return rows

    def _trim_trailing_empty_cells(self, row_values: list[Any]) -> list[str]:
        """移除行尾连续空单元格。"""
        values = [str(value).strip() if value is not None else "" for value in row_values]
        while values and not values[-1]:
            values.pop()
        return values

    def _is_empty_row(self, row_values: list[str]) -> bool:
        """判断一行是否为空。"""
        return not any(str(value).strip() for value in row_values)

    def _is_empty_record(self, record: dict[str, Any]) -> bool:
        """判断映射后的记录是否为空。"""
        return not any(str(value).strip() for value in record.values())

    def _is_numeric_like(self, value: Any) -> bool:
        """判断值是否近似数字列名。"""
        text = str(value).strip()
        return bool(text) and text.isdigit()

    def _read_excel_via_com(self, excel_path: Path) -> pd.DataFrame:
        """通过 Excel COM 转存旧版 xls。"""
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
        """规范化单元格并移除全空列。"""
        normalized = dataframe.copy()
        normalized.columns = [self._normalize_cell(column) for column in normalized.columns]
        if normalized.empty:
            return normalized

        normalized = normalized.map(self._normalize_cell)
        normalized = normalized.loc[:, ~(normalized.eq("").all())]
        return normalized.map(self._normalize_cell)

    def _normalize_cell(self, value: Any) -> Any:
        """规范化单元格值。"""
        if pd.isna(value):
            return ""
        if isinstance(value, str):
            return re.sub(r"\s+", " ", value).strip()
        return value
