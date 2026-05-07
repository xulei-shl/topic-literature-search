"""高级检索批量导出共享骨架。"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from math import ceil
from pathlib import Path
from typing import Optional

from src.core.advanced_export_types import (
    AdvancedExportResult,
    BatchSelectionResult,
    ExportBatchFiles,
    ResumeRuntime,
    SearchParams,
)
from src.utils.result_output import build_batch_output_filename, build_summary_output_filename

logger = logging.getLogger(__name__)


class BaseAdvancedExportFlow(ABC):
    """提供高级检索批量导出的公共主流程。"""

    EXPORT_BATCH_SIZE = 500
    ADVANCED_FORM_READY_SELECTORS: tuple[str, ...] = ()
    CLEANED_METADATA_KIND = "metadata-cleaned"
    ENRICHED_BATCH_KIND = "enriched"

    def run_advanced_export(
        self,
        cli_params: SearchParams,
        progress_file: Optional[Path] = None,
    ) -> AdvancedExportResult:
        """执行高级检索批量导出主流程。

        Args:
            cli_params: 命令行输入参数。
            progress_file: 可选进度文件路径。

        Returns:
            AdvancedExportResult: 导出结果。
        """
        progress_store, resume_data = self._prepare_progress_store(
            progress_file=progress_file,
            cli_params=cli_params,
        )
        resolved_params = type(progress_store).resolve_search_params(
            cli_params=cli_params,
            progress_data=resume_data,
        )
        query = str(resolved_params["query"]).strip()

        self._open_advanced_search_page()
        if self.ADVANCED_FORM_READY_SELECTORS:
            self._wait_for_any_selector(list(self.ADVANCED_FORM_READY_SELECTORS))
        self._ensure_captcha_cleared()
        self._fill_advanced_search_form_from_params(resolved_params)
        self._submit_advanced_search()
        self._wait_for_results_ready()

        summary = self.parser.parse_results_summary()
        total = int(summary["total"])
        planned_download = self._normalize_download_limit(
            resolved_params.get("max_download"),
            total,
        )
        output_dir = self._resolve_output_dir(query, resume_data)

        self.browser_manager.save_session(
            page_type="results",
            last_query=query,
            last_results_url=self.page.url,
            last_result_command="advanced-search",
        )

        if total <= 0:
            self._save_progress_snapshot_for_flow(
                progress_store=progress_store,
                status="no_results",
                search_params=resolved_params,
                output_dir=output_dir,
                planned_download=0,
                batch_count=0,
                exported_total=0,
                exported_batches=0,
                next_batch_index=1,
                current_row_offset=0,
                enriched_batch_files=[],
                final_file_path="",
            )
            return self._build_result_payload(
                search_params=resolved_params,
                total=0,
                exported_total=0,
                planned_download=0,
                batch_count=0,
                exported_batches=0,
                output_dir=output_dir,
                final_file_path="",
                intermediate_files=[],
                progress_file=str(progress_store.file_path),
                resumed_from_progress=resume_data is not None,
                status="no_results",
            )

        batch_count = ceil(planned_download / self.EXPORT_BATCH_SIZE)
        self._prepare_results_page_for_export(planned_download=planned_download, total=total)

        progress_runtime = self._build_resume_runtime(
            resume_data=resume_data,
            output_dir=output_dir,
            planned_download=planned_download,
            batch_count=batch_count,
            total=total,
        )
        current_row_offset = int(progress_runtime["current_row_offset"])
        exported_total = int(progress_runtime["exported_total"])
        exported_batches = int(progress_runtime["exported_batches"])
        next_batch_index = int(progress_runtime["next_batch_index"])
        intermediate_files: list[str] = []
        enriched_batch_files = list(progress_runtime["enriched_batch_files"])

        self._restore_results_position(int(progress_runtime["current_page"]))
        self._save_progress_snapshot_for_flow(
            progress_store=progress_store,
            status="running",
            search_params=resolved_params,
            output_dir=output_dir,
            planned_download=planned_download,
            batch_count=batch_count,
            exported_total=exported_total,
            exported_batches=exported_batches,
            next_batch_index=next_batch_index,
            current_row_offset=current_row_offset,
            enriched_batch_files=enriched_batch_files,
            final_file_path="",
        )

        try:
            for batch_index in range(next_batch_index, batch_count + 1):
                batch_target = min(self.EXPORT_BATCH_SIZE, planned_download - exported_total)
                if batch_target <= 0:
                    break

                self._clear_selected_results()
                batch_selection = self._select_batch_results(batch_target, current_row_offset)
                batch_selection["restore_results_page"] = exported_total + batch_target < planned_download
                batch_download_started_at = time.perf_counter()
                batch_files = self._export_selected_results_for_batch(
                    query=query,
                    batch_index=batch_index,
                    output_dir=output_dir,
                    batch_selection=batch_selection,
                )
                logger.info(
                    "批次下载阶段结束，准备进入本地处理: batch=%s, elapsed_ms=%s, excel=%s, txt=%s",
                    batch_index,
                    int((time.perf_counter() - batch_download_started_at) * 1000),
                    batch_files["excel"],
                    batch_files["txt"],
                )

                cleaned_excel_path = output_dir / build_batch_output_filename(
                    query=query,
                    batch_index=batch_index,
                    kind=self.CLEANED_METADATA_KIND,
                    suffix=".xlsx",
                )
                logger.info("开始清理导出表格: batch=%s, excel=%s", batch_index, batch_files["excel"])
                cleaned_excel_file = self.export_processor.sanitize_export_excel(
                    excel_path=Path(batch_files["excel"]),
                    output_path=cleaned_excel_path,
                )
                try:
                    Path(batch_files["excel"]).unlink(missing_ok=True)
                except Exception as exc:
                    logger.debug("删除原始元数据文件失败: %s", exc)

                enriched_path = output_dir / build_batch_output_filename(
                    query=query,
                    batch_index=batch_index,
                    kind=self.ENRICHED_BATCH_KIND,
                    suffix=".xlsx",
                )
                logger.info(
                    "开始回填参考格式: batch=%s, excel=%s, txt=%s",
                    batch_index,
                    cleaned_excel_file,
                    batch_files["txt"],
                )
                enriched_file = self.export_processor.enrich_batch_excel(
                    excel_path=Path(cleaned_excel_file),
                    txt_path=Path(batch_files["txt"]),
                    output_path=enriched_path,
                )

                exported_total += int(batch_selection["selected_count"])
                exported_batches += 1
                current_row_offset = self._prepare_next_batch_cursor(batch_selection)
                enriched_batch_files.append(Path(enriched_file))
                intermediate_files.extend([cleaned_excel_file, batch_files["txt"], enriched_file])
                self._save_progress_snapshot_for_flow(
                    progress_store=progress_store,
                    status="running",
                    search_params=resolved_params,
                    output_dir=output_dir,
                    planned_download=planned_download,
                    batch_count=batch_count,
                    exported_total=exported_total,
                    exported_batches=exported_batches,
                    next_batch_index=batch_index + 1,
                    current_row_offset=current_row_offset,
                    enriched_batch_files=enriched_batch_files,
                    final_file_path="",
                )
        except KeyboardInterrupt as exc:
            self._save_progress_snapshot_for_flow(
                progress_store=progress_store,
                status="interrupted",
                search_params=resolved_params,
                output_dir=output_dir,
                planned_download=planned_download,
                batch_count=batch_count,
                exported_total=exported_total,
                exported_batches=exported_batches,
                next_batch_index=exported_batches + 1,
                current_row_offset=current_row_offset,
                enriched_batch_files=enriched_batch_files,
                final_file_path="",
                error=exc,
            )
            raise
        except Exception as exc:
            self._save_progress_snapshot_for_flow(
                progress_store=progress_store,
                status="failed",
                search_params=resolved_params,
                output_dir=output_dir,
                planned_download=planned_download,
                batch_count=batch_count,
                exported_total=exported_total,
                exported_batches=exported_batches,
                next_batch_index=exported_batches + 1,
                current_row_offset=current_row_offset,
                enriched_batch_files=enriched_batch_files,
                final_file_path="",
                error=exc,
            )
            raise

        final_file_path = ""
        if enriched_batch_files:
            final_file = output_dir / build_summary_output_filename(query=query)
            final_file_path = self.export_processor.merge_batch_excels(enriched_batch_files, final_file)

        self._save_progress_snapshot_for_flow(
            progress_store=progress_store,
            status="success",
            search_params=resolved_params,
            output_dir=output_dir,
            planned_download=planned_download,
            batch_count=batch_count,
            exported_total=exported_total,
            exported_batches=exported_batches,
            next_batch_index=batch_count + 1,
            current_row_offset=current_row_offset,
            enriched_batch_files=enriched_batch_files,
            final_file_path=final_file_path,
        )
        return self._build_result_payload(
            search_params=resolved_params,
            total=total,
            exported_total=exported_total,
            planned_download=planned_download,
            batch_count=batch_count,
            exported_batches=exported_batches,
            output_dir=output_dir,
            final_file_path=final_file_path,
            intermediate_files=intermediate_files,
            progress_file=str(progress_store.file_path),
            resumed_from_progress=resume_data is not None,
            status="success",
        )

    def _prepare_results_page_for_export(self, planned_download: int, total: int) -> None:
        """在批量导出前执行站点级结果页预处理。"""
        del planned_download, total

    def _prepare_next_batch_cursor(self, batch_selection: BatchSelectionResult) -> int:
        """根据批次勾选结果计算下一批起始偏移量。"""
        return int(batch_selection.get("next_row_offset") or 0)

    def _build_result_payload(
        self,
        search_params: SearchParams,
        total: int,
        exported_total: int,
        planned_download: int,
        batch_count: int,
        exported_batches: int,
        output_dir: Path,
        final_file_path: str,
        intermediate_files: list[str],
        progress_file: str,
        resumed_from_progress: bool,
        status: str,
    ) -> AdvancedExportResult:
        """构造统一返回结果。"""
        query = str(search_params["query"]).strip()
        return {
            "result_type": "advanced_export",
            "status": status,
            "query": query,
            "total": total,
            "selected": exported_total,
            "exported": exported_total,
            "planned_download": planned_download,
            "batch_count": batch_count,
            "exported_batches": exported_batches,
            "core_only": bool(search_params.get("core_only")),
            "date_from": search_params.get("date_from"),
            "date_to": search_params.get("date_to"),
            "date_range": self._format_date_range(
                search_params.get("date_from"),
                search_params.get("date_to"),
            ),
            "url": self.page.url,
            "file_path": final_file_path,
            "final_file_path": final_file_path,
            "output_dir": str(output_dir),
            "intermediate_files": intermediate_files,
            "progress_file": progress_file,
            "resumed_from_progress": resumed_from_progress,
        }

    @abstractmethod
    def _prepare_progress_store(self, progress_file: Optional[Path], cli_params: SearchParams):
        """初始化进度文件存储。"""

    @abstractmethod
    def _resolve_output_dir(self, query: str, resume_data: Optional[SearchParams]) -> Path:
        """解析输出目录。"""

    @abstractmethod
    def _build_resume_runtime(
        self,
        resume_data: Optional[SearchParams],
        output_dir: Path,
        planned_download: int,
        batch_count: int,
        total: int,
    ) -> ResumeRuntime:
        """构造恢复运行态。"""

    @abstractmethod
    def _save_progress_snapshot_for_flow(
        self,
        progress_store,
        status: str,
        search_params: SearchParams,
        output_dir: Path,
        planned_download: int,
        batch_count: int,
        exported_total: int,
        exported_batches: int,
        next_batch_index: int,
        current_row_offset: int,
        enriched_batch_files: list[Path],
        final_file_path: str,
        error: Optional[BaseException] = None,
    ) -> None:
        """写入当前进度快照。"""

    @abstractmethod
    def _open_advanced_search_page(self) -> None:
        """打开高级检索页。"""

    @abstractmethod
    def _fill_advanced_search_form_from_params(self, search_params: SearchParams) -> None:
        """根据解析后的检索参数填写高级检索表单。"""

    @abstractmethod
    def _submit_advanced_search(self) -> None:
        """提交高级检索。"""

    @abstractmethod
    def _restore_results_position(self, target_page: int) -> None:
        """恢复结果页位置。"""

    @abstractmethod
    def _clear_selected_results(self) -> None:
        """清空当前已选结果。"""

    @abstractmethod
    def _select_batch_results(self, export_limit: int, row_offset: int) -> BatchSelectionResult:
        """勾选当前批次结果。"""

    @abstractmethod
    def _export_selected_results_for_batch(
        self,
        query: str,
        batch_index: int,
        output_dir: Path,
        batch_selection: BatchSelectionResult,
    ) -> ExportBatchFiles:
        """导出当前批次结果。"""

    @abstractmethod
    def _wait_for_any_selector(self, selectors: list[str], timeout: Optional[int] = None) -> None:
        """等待任一元素出现。"""

    @abstractmethod
    def _wait_for_results_ready(self) -> None:
        """等待结果页完成加载。"""

    @abstractmethod
    def _ensure_captcha_cleared(self) -> None:
        """确保验证码已处理。"""

    @abstractmethod
    def _format_date_range(self, date_from: Optional[str], date_to: Optional[str]) -> str:
        """格式化日期范围。"""

    @abstractmethod
    def _normalize_download_limit(self, num_results: Optional[int], total_results: int) -> int:
        """标准化最大下载量。"""
