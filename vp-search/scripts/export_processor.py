"""维普导出结果本地处理。"""

from io import StringIO
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
from win32com.client import DispatchEx

from exceptions import ExportProcessingError

logger = logging.getLogger("vp_search.export_processor")

REFERENCE_COLUMN = "参考格式"


class ExportResultProcessor:
    """负责处理维普导出的表格与引文文本。"""

    def sanitize_export_excel(self, excel_path: Path, output_path: Path) -> str:
        """清理导出表格中的重复表头并重新保存。"""
        logger.info("开始清理导出表格", extra={"excel_path": str(excel_path), "output_path": str(output_path)})
        dataframe = self._sanitize_export_dataframe(self._read_export_table(excel_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        dataframe.to_excel(output_path, index=False, engine="openpyxl")
        logger.info("导出表格已清理完成", extra={"excel_path": str(excel_path), "output_path": str(output_path)})
        return str(output_path)

    def parse_reference_txt(self, txt_path: Path) -> list[str]:
        """解析参考文献文本。"""
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
        logger.info(
            "开始回填参考格式",
            extra={"excel_path": str(excel_path), "txt_path": str(txt_path), "output_path": str(output_path)},
        )
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
            excel_paths: 待合并的 Excel 文件路径列表。
            output_path: 合并后输出路径。
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
        logger.info("已生成最终汇总文件", extra={"output_path": str(output_path), "batches": len(excel_paths)})
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
        header_row = self._infer_initial_headers(dataframe) or self._find_header_row(rows)
        if not header_row:
            return pd.DataFrame()

        records: list[dict[str, Any]] = []
        for row_values in rows:
            if self._is_empty_row(row_values):
                continue
            if self._is_duplicate_header_row(row_values, header_row):
                continue
            if len(row_values) < len(header_row):
                row_values = row_values + [""] * (len(header_row) - len(row_values))
            record = {
                header_row[index]: row_values[index] if index < len(row_values) else ""
                for index in range(len(header_row))
                if header_row[index]
            }
            if self._is_empty_record(record):
                continue
            records.append(record)

        sanitized = pd.DataFrame(records)
        if sanitized.empty:
            return pd.DataFrame(columns=header_row)
        for header in header_row:
            if header and header not in sanitized.columns:
                sanitized[header] = ""
        return sanitized.reindex(columns=[header for header in header_row if header]).fillna("")

    def _infer_initial_headers(self, dataframe: pd.DataFrame) -> list[str]:
        normalized_columns = [self._normalize_cell(column) for column in dataframe.columns.tolist()]
        if not normalized_columns:
            return []
        if all(str(column).strip().isdigit() for column in normalized_columns if str(column).strip()):
            return []
        return [str(column).strip() for column in normalized_columns]

    def _find_header_row(self, rows: list[list[str]]) -> list[str]:
        for row_values in rows:
            if self._is_empty_row(row_values):
                continue
            normalized_row = [value for value in row_values if value]
            if len(normalized_row) >= 2:
                return [self._normalize_cell(value) for value in row_values]
        return []

    def _is_duplicate_header_row(self, row_values: list[str], header_row: list[str]) -> bool:
        normalized_row = [self._normalize_cell(value) for value in row_values]
        if normalized_row == header_row:
            return True
        if normalized_row[: len(header_row)] == header_row:
            return True
        return False

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
