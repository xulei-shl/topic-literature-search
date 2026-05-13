"""CNKI ????????????"""

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams
from src.utils.playwright_page import click_first_available, disable_checkbox, enable_checkbox, first_visible_locator, set_input_value, wait_for_any_selector
from src.utils.result_output import build_export_file_path
from src.utils.search_timeout import find_env_path, parse_env_file

from browser import BrowserManager
from config import CnkiSearchConfig
from export_processor import ExportResultProcessor
from exceptions import CaptchaError, NavigationStateError, TimeoutError, ValidationError
from progress_store import SearchProgressStore
from result_parser import ResultParser
from utils import build_output_slug

logger = logging.getLogger("cnki_search.interactor")

class CnkiProgressMixin:
    """CNKI ????????????"""

    def _prepare_progress_store(
        self,
        progress_file: Optional[Path],
        cli_params: Dict[str, Any],
    ) -> tuple[SearchProgressStore, Optional[Dict[str, Any]]]:
        """初始化进度文件存储并按需读取历史进度。"""
        query = cli_params.get("query")
        output_dir = self.config.ensure_output_dir(query) if query else None
        return SearchProgressStore.prepare_store(
            progress_file=progress_file,
            output_dir=output_dir,
            query=query,
            date_from=cli_params.get("date_from"),
            date_to=cli_params.get("date_to"),
            core_only=bool(cli_params.get("core_only")),
            include_no_fulltext=bool(cli_params.get("include_no_fulltext")),
            max_download=cli_params.get("max_download"),
        )

    def _resolve_output_dir(self, query: str, resume_data: Optional[Dict[str, Any]]) -> Path:
        """返回当前任务应使用的输出目录。"""
        runtime = (resume_data or {}).get("runtime") or {}
        output_dir = runtime.get("output_dir")
        if output_dir:
            return Path(output_dir).resolve()
        return self.config.ensure_output_dir(query)

    def _build_resume_runtime(
        self,
        resume_data: Optional[Dict[str, Any]],
        output_dir: Path,
        planned_download: int,
        batch_count: int,
        total: int,
    ) -> Dict[str, Any]:
        """根据历史进度构造恢复运行态。"""
        if not resume_data:
            return {
                "exported_total": 0,
                "exported_batches": 0,
                "next_batch_index": 1,
                "current_page": 1,
                "current_row_offset": 0,
                "enriched_batch_files": [],
            }

        status = resume_data.get("status")
        if status == "success":
            raise ValidationError("该进度文件已执行完成，无需继续")
        if status == "no_results":
            raise ValidationError("该进度文件对应的检索结果为空，无需继续")

        runtime = resume_data.get("runtime") or {}
        exported_total = int(runtime.get("exported_total") or 0)
        exported_batches = int(runtime.get("exported_batches") or 0)
        next_batch_index = int(runtime.get("next_batch_index") or (exported_batches + 1))
        current_page = int(runtime.get("current_page") or 1)
        current_row_offset = int(runtime.get("current_row_offset") or 0)
        enriched_batch_files = [Path(file_path).resolve() for file_path in runtime.get("enriched_batch_files") or []]

        valid_batch_files = self._validate_resume_batch_files(enriched_batch_files)
        if len(valid_batch_files) != len(enriched_batch_files):
            logger.warning("部分已导出批次文件缺失，重置导出计数为 0，从头开始导出")
            exported_total = 0
            exported_batches = 0
            next_batch_index = 1
            current_page = 1
            current_row_offset = 0
            enriched_batch_files = []
        else:
            enriched_batch_files = valid_batch_files

        if exported_total > planned_download:
            raise ValidationError("进度文件中的已导出数量超过本次计划导出数量")
        if exported_total > total:
            raise ValidationError("当前检索结果总数小于进度文件中的已导出数量，无法安全恢复")
        if exported_batches > batch_count:
            raise ValidationError("进度文件中的已导出批次数超过本次批次数")

        return {
            "exported_total": exported_total,
            "exported_batches": exported_batches,
            "next_batch_index": next_batch_index,
            "current_page": current_page,
            "current_row_offset": current_row_offset,
            "enriched_batch_files": enriched_batch_files,
            "output_dir": output_dir.resolve(),
        }

    def _validate_resume_batch_files(self, enriched_batch_files: list[Path]) -> list[Path]:
        """校验历史批次文件是否仍然存在，返回有效文件列表。"""
        valid_files: list[Path] = []
        for file_path in enriched_batch_files:
            if file_path.exists():
                valid_files.append(file_path)
            else:
                logger.warning("进度文件引用的批次文件不存在，已跳过: %s", file_path)
        return valid_files

    def _save_progress_snapshot(
        self,
        progress_store: SearchProgressStore,
        status: str,
        query: str,
        date_from: Optional[str],
        date_to: Optional[str],
        core_only: bool,
        include_no_fulltext: bool,
        max_download: Optional[int],
        output_dir: Path,
        planned_download: int,
        batch_count: int,
        exported_total: int,
        exported_batches: int,
        next_batch_index: int,
        current_page: int,
        current_row_offset: int,
        enriched_batch_files: list[Path],
        final_file_path: str,
        error: Optional[BaseException] = None,
    ) -> None:
        """写入当前高级检索进度快照。"""
        page_context = self._build_resume_page_context(current_page)
        state: Dict[str, Any] = {
            "version": SearchProgressStore.VERSION,
            "status": status,
            "search_params": {
                "query": query,
                "date_from": date_from,
                "date_to": date_to,
                "core_only": core_only,
                "include_no_fulltext": include_no_fulltext,
                "max_download": max_download,
            },
            "runtime": {
                "output_dir": str(output_dir.resolve()),
                "planned_download": planned_download,
                "batch_count": batch_count,
                "exported_total": exported_total,
                "exported_batches": exported_batches,
                "next_batch_index": next_batch_index,
                "current_page": current_page,
                "page_text": page_context["page_text"],
                "current_row_offset": current_row_offset,
                "enriched_batch_files": [str(file_path.resolve()) for file_path in enriched_batch_files],
                "final_file_path": final_file_path,
                "last_known_url": page_context["url"],
            },
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        if error is not None:
            state["last_error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }

        progress_store.save(state)
        logger.info(
            "高级检索进度已写入: status=%s, file=%s, batches=%s/%s",
            status,
            progress_store.file_path,
            exported_batches,
            batch_count,
        )

    def _build_resume_page_context(self, current_page: int) -> Dict[str, Any]:
        """基于恢复游标构造进度页上下文。"""
        page_context = self._safe_progress_page_context()
        page_text = page_context.get("page_text", "")
        total_pages = self._extract_total_pages(page_text)
        return {
            "current_page": current_page,
            "page_text": f"{current_page}/{total_pages}" if total_pages > 0 else str(current_page),
            "url": page_context.get("url", self.page.url if self.page else ""),
        }

    def _extract_total_pages(self, page_text: str) -> int:
        """从页码文本中提取总页数。"""
        if not page_text:
            return 0
        match = re.search(r"/\s*(\d+)", page_text)
        if not match:
            return 0
        return int(match.group(1))

    def _safe_progress_page_context(self) -> Dict[str, Any]:
        """尽量提取结果页上下文，用于进度留痕。"""
        try:
            summary = self.parser.parse_results_summary()
            return {
                "current_page": int(summary.get("current_page") or 1),
                "page_text": summary.get("page", ""),
                "url": self.page.url,
            }
        except Exception as exc:
            logger.debug("提取进度页上下文失败: %s", exc)
            return {
                "current_page": 1,
                "page_text": "",
                "url": self.page.url if self.page else "",
            }
