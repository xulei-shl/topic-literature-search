"""高级检索批量导出共享类型。"""

from pathlib import Path
from typing import Any, TypedDict


class ResumeRuntime(TypedDict, total=False):
    """断点续跑运行态。"""

    exported_total: int
    exported_batches: int
    next_batch_index: int
    current_page: int
    current_row_offset: int
    enriched_batch_files: list[Path]
    output_dir: Path


class BatchSelectionResult(TypedDict, total=False):
    """单批次勾选结果。"""

    selected_count: int
    next_row_offset: int
    page_row_count: int
    already_at_target: bool
    restore_results_page: bool


class ExportBatchFiles(TypedDict):
    """单批次导出文件。"""

    excel: str
    txt: str


class AdvancedExportResult(TypedDict):
    """高级检索导出返回结构。"""

    result_type: str
    status: str
    query: str
    total: int
    selected: int
    exported: int
    planned_download: int
    batch_count: int
    exported_batches: int
    core_only: bool
    date_from: str | None
    date_to: str | None
    date_range: str
    url: str
    file_path: str
    final_file_path: str
    output_dir: str
    intermediate_files: list[str]
    progress_file: str
    resumed_from_progress: bool


SearchParams = dict[str, Any]
