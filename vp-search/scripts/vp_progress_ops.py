"""??????????????"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from src.core.advanced_export_flow import BaseAdvancedExportFlow
from src.core.advanced_export_types import BatchSelectionResult, SearchParams
from src.utils.playwright_page import click_first_available, disable_checkbox, enable_checkbox, has_visible_selector, set_native_select_value, wait_for_any_selector
from src.utils.result_output import build_export_file_path

from browser import BrowserManager
from config import VpSearchConfig
from exceptions import CaptchaError, TimeoutError, ValidationError
from export_processor import ExportResultProcessor
from progress_store import SearchProgressStore
from result_parser import ResultParser
from utils import build_output_slug

logger = logging.getLogger("vp_search.interactor")

class VpProgressMixin:
    """??????????????"""

    def _build_progress_page_context(self) -> Dict[str, Any]:
        """提取当前结果页上下文。"""
        summary = self.parser.parse_results_summary()
        return {
            "current_page": int(summary.get("current_page") or 1),
            "page_text": summary.get("page", ""),
            "url": self.page.url,
        }

    def _cache_progress_page_context(self) -> None:
        """缓存最近一次可用的结果页上下文。"""
        try:
            self._last_results_page_context = self._build_progress_page_context()
        except Exception as exc:
            logger.debug("缓存结果页上下文失败: %s", exc)

    def _prepare_progress_store(
        self,
        progress_file: Optional[Path],
        cli_params: Dict[str, Any],
    ) -> tuple[SearchProgressStore, Optional[Dict[str, Any]]]:
        """初始化进度文件存储并按需读取历史进度。"""
        if progress_file is not None:
            progress_store = SearchProgressStore(progress_file)
            resume_data = progress_store.load() if progress_store.exists() else None
            return progress_store, resume_data

        query = cli_params.get("query")
        if not query:
            raise ValidationError("高级检索至少需要提供 --query 或 --progress-file")

        output_dir = self.config.ensure_output_dir(query)
        default_path = SearchProgressStore.build_default_path(
            output_dir=output_dir,
            query=query,
            date_from=cli_params.get("date_from"),
            date_to=cli_params.get("date_to"),
            core_only=bool(cli_params.get("core_only")),
            max_download=cli_params.get("max_download"),
        )
        return SearchProgressStore(default_path), None

    def _resolve_output_dir(self, query: str, resume_data: Optional[Dict[str, Any]]) -> Path:
        """返回当前任务应使用的输出目录。"""
        runtime = (resume_data or {}).get("runtime") or {}
        output_dir = runtime.get("output_dir")
        if output_dir:
            resolved_dir = Path(output_dir).resolve()
            resolved_dir.mkdir(parents=True, exist_ok=True)
            return resolved_dir
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

        self._validate_resume_batch_files(enriched_batch_files)

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

    def _validate_resume_batch_files(self, enriched_batch_files: list[Path]) -> None:
        """校验历史批次文件是否仍然存在。"""
        for file_path in enriched_batch_files:
            if not file_path.exists():
                raise ValidationError(f"进度文件引用的批次文件不存在: {file_path}")

    def _save_progress_snapshot(
        self,
        progress_store: SearchProgressStore,
        status: str,
        query: str,
        date_from: Optional[str],
        date_to: Optional[str],
        core_only: bool,
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
            page_context = self._build_progress_page_context()
            self._last_results_page_context = page_context
            return page_context
        except Exception as exc:
            logger.debug("提取进度页上下文失败: %s", exc)
            cached_context = getattr(self, "_last_results_page_context", None)
            if cached_context:
                return dict(cached_context)
            return {
                "current_page": 1,
                "page_text": "",
                "url": self.page.url if self.page else "",
            }
