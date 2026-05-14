"""万方截至年份逐年导出编排。"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.utils.result_output import build_summary_output_filename, build_summary_report_output_filename
from src.utils.yearly_export_validation import (
    cleanup_year_output_dir,
    count_excel_rows,
    find_latest_yearly_merged_excel,
    find_yearly_no_results_note,
    find_latest_yearly_summary_report,
    read_report_total,
)

from exceptions import ValidationError
from progress_store import SearchProgressStore
from wanfang_yearly_progress_store import YearlySearchProgressStore

logger = logging.getLogger("wanfang_search.yearly_export")


class WanfangYearlyExportMixin:
    """负责截至年份全量导出的逐年外层编排。"""

    YEARLY_MIN_YEAR = 1949
    YEARLY_VALIDATION_MAX_RETRIES = 2
    ANTI_CRAWL_WAIT_INTERVAL = 5

    def _should_use_yearly_full_export(self, date_to: Optional[str], max_download: Optional[int]) -> bool:
        """判断当前请求是否应切换到逐年全量导出模式。"""
        return bool(date_to) and max_download is None

    def _prepare_yearly_progress_store(
        self,
        progress_file: Optional[Path],
        cli_params: dict[str, Any],
    ) -> tuple[YearlySearchProgressStore, Optional[dict[str, Any]]]:
        """初始化逐年导出外层进度文件。"""
        query = cli_params.get("query")
        output_dir = self.config.ensure_output_dir(query) if query else None
        return YearlySearchProgressStore.prepare_store(
            progress_file=progress_file,
            output_dir=output_dir,
            query=query,
            date_from=cli_params.get("date_from"),
            date_to=cli_params.get("date_to"),
        )

    def _run_yearly_advanced_export(
        self,
        cli_params: dict[str, Any],
        progress_file: Optional[Path],
    ) -> dict[str, Any]:
        """执行截至年份全量导出的逐年编排。"""
        progress_store, resume_data = self._prepare_yearly_progress_store(progress_file, cli_params)
        resolved_params = YearlySearchProgressStore.resolve_search_params(cli_params, resume_data)
        query = str(resolved_params["query"]).strip()
        output_dir = self._resolve_yearly_output_dir(query, resume_data)
        runtime = self._build_yearly_runtime(resume_data, output_dir)
        tasks = self._resolve_yearly_tasks(resolved_params, runtime)
        self._ensure_yearly_search_page_ready()

        if not tasks:
            report_file = self._write_yearly_summary_report(
                output_dir=output_dir,
                query=query,
                search_params=resolved_params,
                status="no_results",
                total=0,
                planned_download=0,
                exported_total=0,
                batch_count=0,
                exported_batches=0,
                executed_years=[],
                empty_years=[],
                final_file_path="",
                progress_file=str(progress_store.file_path),
            )
            self._save_yearly_progress_snapshot(
                progress_store=progress_store,
                status="no_results",
                search_params=resolved_params,
                output_dir=output_dir,
                available_years=[],
                next_year_index=0,
                executed_years=[],
                empty_years=[],
                yearly_result_files=[],
                batch_report_files=[],
                yearly_report_files=[],
                empty_result_files=[],
                current_year="",
                current_year_date_from="",
                current_year_date_to="",
                current_year_progress_file="",
                total=0,
                planned_download=0,
                exported_total=0,
                batch_count=0,
                exported_batches=0,
                final_file_path="",
            )
            return self._build_yearly_result_payload(
                search_params=resolved_params,
                output_dir=output_dir,
                status="no_results",
                total=0,
                planned_download=0,
                exported_total=0,
                batch_count=0,
                exported_batches=0,
                final_file_path="",
                report_file=report_file,
                progress_file=str(progress_store.file_path),
                batch_report_files=[],
                intermediate_files=[report_file],
                executed_years=[],
                empty_years=[],
                resumed_from_progress=resume_data is not None,
            )

        available_years = [task["year"] for task in tasks]
        next_year_index = int(runtime.get("next_year_index", 0))
        executed_years = list(runtime.get("executed_years") or [])
        empty_years = list(runtime.get("empty_years") or [])
        yearly_result_files = list(runtime.get("yearly_result_files") or [])
        batch_report_files = list(runtime.get("batch_report_files") or [])
        yearly_report_files = list(runtime.get("yearly_report_files") or [])
        empty_result_files = list(runtime.get("empty_result_files") or [])
        total = int(runtime.get("total") or 0)
        planned_download = int(runtime.get("planned_download") or 0)
        exported_total = int(runtime.get("exported_total") or 0)
        batch_count = int(runtime.get("batch_count") or 0)
        exported_batches = int(runtime.get("exported_batches") or 0)
        validation_rerun_in_progress = bool(runtime.get("validation_rerun_in_progress", False))

        if validation_rerun_in_progress:
            logger.info("检测到校验重跑中断记录，跳过外层年度循环，直接进入校验重跑阶段")
            next_year_index = len(tasks)

        self._save_yearly_progress_snapshot(
            progress_store=progress_store,
            status="running",
            search_params=resolved_params,
            output_dir=output_dir,
            available_years=available_years,
            next_year_index=next_year_index,
            executed_years=executed_years,
            empty_years=empty_years,
            yearly_result_files=yearly_result_files,
            batch_report_files=batch_report_files,
            yearly_report_files=yearly_report_files,
            empty_result_files=empty_result_files,
            current_year=str(runtime.get("current_year") or ""),
            current_year_date_from=str(runtime.get("current_year_date_from") or ""),
            current_year_date_to=str(runtime.get("current_year_date_to") or ""),
            current_year_progress_file=str(runtime.get("current_year_progress_file") or ""),
            total=total,
            planned_download=planned_download,
            exported_total=exported_total,
            batch_count=batch_count,
            exported_batches=exported_batches,
            final_file_path=str(runtime.get("final_file_path") or ""),
        )

        try:
            for year_index in range(next_year_index, len(tasks)):
                task = tasks[year_index]
                sub_progress_file = self._build_yearly_sub_progress_file(output_dir, task)
                self._save_yearly_progress_snapshot(
                    progress_store=progress_store,
                    status="running",
                    search_params=resolved_params,
                    output_dir=output_dir,
                    available_years=available_years,
                    next_year_index=year_index,
                    executed_years=executed_years,
                    empty_years=empty_years,
                    yearly_result_files=yearly_result_files,
                    batch_report_files=batch_report_files,
                    yearly_report_files=yearly_report_files,
                    empty_result_files=empty_result_files,
                    current_year=task["year"],
                    current_year_date_from=task.get("date_from") or "",
                    current_year_date_to=task["date_to"],
                    current_year_progress_file=str(sub_progress_file),
                    total=total,
                    planned_download=planned_download,
                    exported_total=exported_total,
                    batch_count=batch_count,
                    exported_batches=exported_batches,
                    final_file_path="",
                )
                result = self._run_single_yearly_export(task, output_dir, sub_progress_file)
                executed_years.append(task["year"])
                total += int(result.get("total") or 0)
                planned_download += int(result.get("planned_download") or 0)
                exported_total += int(result.get("exported") or 0)
                batch_count += int(result.get("batch_count") or 0)
                exported_batches += int(result.get("exported_batches") or 0)

                if result.get("status") == "no_results":
                    empty_years.append(task["year"])
                    empty_result_files.append(self._write_empty_year_result(output_dir, task, result))
                else:
                    final_file_path = str(result.get("final_file_path") or "")
                    if final_file_path:
                        yearly_result_files.append(final_file_path)
                    batch_report_files.extend(result.get("batch_report_files") or [])
                    if result.get("report_file"):
                        yearly_report_files.append(str(result["report_file"]))

                self._save_yearly_progress_snapshot(
                    progress_store=progress_store,
                    status="running",
                    search_params=resolved_params,
                    output_dir=output_dir,
                    available_years=available_years,
                    next_year_index=year_index + 1,
                    executed_years=executed_years,
                    empty_years=empty_years,
                    yearly_result_files=yearly_result_files,
                    batch_report_files=batch_report_files,
                    yearly_report_files=yearly_report_files,
                    empty_result_files=empty_result_files,
                    current_year="",
                    current_year_date_from="",
                    current_year_date_to="",
                    current_year_progress_file="",
                    total=total,
                    planned_download=planned_download,
                    exported_total=exported_total,
                    batch_count=batch_count,
                    exported_batches=exported_batches,
                    final_file_path="",
                )

                if (year_index + 1) % self.ANTI_CRAWL_WAIT_INTERVAL == 0:
                    self._close_browser_for_wait()
                    wait_seconds = random.uniform(15, 25)
                    logger.info(f"已导出 {self.ANTI_CRAWL_WAIT_INTERVAL} 年，关闭浏览器等待 {wait_seconds:.1f} 秒以避免反爬预警")
                    time.sleep(wait_seconds)
                    self.browser_manager.start()
                    self._ensure_yearly_search_page_ready()
        except KeyboardInterrupt as exc:
            self._save_yearly_progress_snapshot(
                progress_store=progress_store,
                status="interrupted",
                search_params=resolved_params,
                output_dir=output_dir,
                available_years=available_years,
                next_year_index=year_index,
                executed_years=executed_years,
                empty_years=empty_years,
                yearly_result_files=yearly_result_files,
                batch_report_files=batch_report_files,
                yearly_report_files=yearly_report_files,
                empty_result_files=empty_result_files,
                current_year=task["year"],
                current_year_date_from=task.get("date_from") or "",
                current_year_date_to=task["date_to"],
                current_year_progress_file=str(sub_progress_file),
                total=total,
                planned_download=planned_download,
                exported_total=exported_total,
                batch_count=batch_count,
                exported_batches=exported_batches,
                final_file_path="",
                error=exc,
            )
            raise
        except Exception as exc:
            self._save_yearly_progress_snapshot(
                progress_store=progress_store,
                status="failed",
                search_params=resolved_params,
                output_dir=output_dir,
                available_years=available_years,
                next_year_index=year_index,
                executed_years=executed_years,
                empty_years=empty_years,
                yearly_result_files=yearly_result_files,
                batch_report_files=batch_report_files,
                yearly_report_files=yearly_report_files,
                empty_result_files=empty_result_files,
                current_year=task["year"],
                current_year_date_from=task.get("date_from") or "",
                current_year_date_to=task["date_to"],
                current_year_progress_file=str(sub_progress_file),
                total=total,
                planned_download=planned_download,
                exported_total=exported_total,
                batch_count=batch_count,
                exported_batches=exported_batches,
                final_file_path="",
                error=exc,
            )
            raise

        batch_report_files, skipped_year_details = self._rerun_years_until_export_consistent(
            tasks=tasks,
            output_dir=output_dir,
            progress_store=progress_store,
            search_params=resolved_params,
            available_years=available_years,
            executed_years=executed_years,
            empty_years=empty_years,
            yearly_result_files=yearly_result_files,
            batch_report_files=batch_report_files,
            yearly_report_files=yearly_report_files,
            empty_result_files=empty_result_files,
            total=total,
            planned_download=planned_download,
            exported_total=exported_total,
            batch_count=batch_count,
            exported_batches=exported_batches,
        )
        yearly_result_files, yearly_report_files = self._rebuild_yearly_output_files(
            tasks=tasks,
            output_dir=output_dir,
            executed_years=executed_years,
            empty_years=empty_years,
            skipped_years=[item["year"] for item in skipped_year_details],
        )

        final_file_path = ""
        if yearly_result_files:
            final_file = output_dir / build_summary_output_filename(query=query, fallback="wanfang-yearly")
            final_file_path = self.export_processor.merge_batch_excels(
                [Path(path) for path in yearly_result_files], final_file, check_reference_column=True
            )

        status = "success" if yearly_result_files or skipped_year_details else "no_results"
        report_file = self._write_yearly_summary_report(
            output_dir=output_dir,
            query=query,
            search_params=resolved_params,
            status=status,
            total=total,
            planned_download=planned_download,
            exported_total=exported_total,
            batch_count=batch_count,
            exported_batches=exported_batches,
            executed_years=executed_years,
            empty_years=empty_years,
            skipped_year_details=skipped_year_details,
            final_file_path=final_file_path,
            progress_file=str(progress_store.file_path),
        )
        self._save_yearly_progress_snapshot(
            progress_store=progress_store,
            status=status,
            search_params=resolved_params,
            output_dir=output_dir,
            available_years=available_years,
            next_year_index=len(tasks),
            executed_years=executed_years,
            empty_years=empty_years,
            yearly_result_files=yearly_result_files,
            batch_report_files=batch_report_files,
            yearly_report_files=yearly_report_files,
            empty_result_files=empty_result_files,
            current_year="",
            current_year_date_from="",
            current_year_date_to="",
            current_year_progress_file="",
            total=total,
            planned_download=planned_download,
            exported_total=exported_total,
            batch_count=batch_count,
            exported_batches=exported_batches,
            final_file_path=final_file_path,
        )
        return self._build_yearly_result_payload(
            search_params=resolved_params,
            output_dir=output_dir,
            status=status,
            total=total,
            planned_download=planned_download,
            exported_total=exported_total,
            batch_count=batch_count,
            exported_batches=exported_batches,
            final_file_path=final_file_path,
            report_file=report_file,
            progress_file=str(progress_store.file_path),
            batch_report_files=batch_report_files,
            intermediate_files=yearly_result_files + yearly_report_files + empty_result_files + [report_file],
            executed_years=executed_years,
            empty_years=empty_years,
            skipped_year_details=skipped_year_details,
            resumed_from_progress=resume_data is not None,
        )

    def _resolve_yearly_output_dir(self, query: str, resume_data: Optional[dict[str, Any]]) -> Path:
        """解析逐年导出根输出目录。"""
        runtime = (resume_data or {}).get("runtime") or {}
        output_dir = runtime.get("output_dir")
        if output_dir:
            resolved = Path(output_dir).resolve()
            resolved.mkdir(parents=True, exist_ok=True)
            return resolved
        resolved = self.config.ensure_output_dir(query)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _build_yearly_runtime(self, resume_data: Optional[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
        """构造逐年编排运行态。"""
        if not resume_data:
            return {"output_dir": str(output_dir.resolve())}
        status = resume_data.get("status")
        if status == "success":
            raise ValidationError("该逐年进度文件已执行完成，无需继续")
        if status == "no_results":
            raise ValidationError("该逐年进度文件对应的检索结果为空，无需继续")
        runtime = dict(resume_data.get("runtime") or {})
        runtime["output_dir"] = str(output_dir.resolve())
        return runtime

    def _resolve_yearly_tasks(self, search_params: dict[str, Any], runtime: dict[str, Any]) -> list[dict[str, Any]]:
        """解析本次逐年导出的任务列表。"""
        available_years = list(runtime.get("available_years") or [])
        if not available_years:
            available_years = self._collect_available_end_years()
        return self._build_yearly_export_tasks(
            query=str(search_params["query"]).strip(),
            available_years=available_years,
            date_from=search_params.get("date_from"),
            date_to=search_params.get("date_to"),
        )

    def _build_yearly_export_tasks(
        self,
        query: str,
        available_years: list[str],
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> list[dict[str, Any]]:
        """基于真实可选年份生成逐年单年任务。"""
        if not date_to:
            return []
        normalized_years = sorted(
            {
                year_text.strip()
                for year_text in available_years
                if str(year_text).strip().isdigit() and int(str(year_text).strip()) >= self.YEARLY_MIN_YEAR
            },
            key=int,
        )
        if not normalized_years:
            return []
        effective_start = max(int(date_from or self.YEARLY_MIN_YEAR), self.YEARLY_MIN_YEAR)
        end_year = int(date_to)
        tasks: list[dict[str, Any]] = []
        for year_text in normalized_years:
            year_value = int(year_text)
            if year_value < effective_start or year_value > end_year:
                continue
            task_year = f"{year_value:04d}"
            tasks.append(
                {
                    "query": query,
                    "year": task_year,
                    "date_from": task_year,
                    "date_to": task_year,
                    "max_download": None,
                }
            )
        return tasks

    def _collect_available_end_years(self) -> list[str]:
        """从结束年下拉框提取真实可选年份。"""
        self._ensure_yearly_search_page_ready()
        trigger = self._get_date_select_trigger("end")
        if trigger.count() == 0:
            return []
        texts = self._collect_date_select_option_texts(trigger)
        years: list[str] = []
        for text in texts:
            match = re.fullmatch(r"(\d{4})年", text.strip())
            if match:
                years.append(match.group(1))
        return years

    def _ensure_yearly_search_page_ready(self) -> None:
        """确保逐年模式复用当前高级检索页，而不是每年重新开页。"""
        self._open_advanced_search_page()
        if self.ADVANCED_FORM_READY_SELECTORS:
            self._wait_for_any_selector(list(self.ADVANCED_FORM_READY_SELECTORS))
        self._ensure_captcha_cleared()

    def _close_browser_for_wait(self) -> None:
        """关闭浏览器以执行反爬等待。"""
        try:
            self.browser_manager.close()
        except Exception:
            pass

    def _build_yearly_sub_progress_file(self, output_dir: Path, task: dict[str, Any]) -> Path:
        """生成单年度子任务的进度文件路径。"""
        year_output_dir = output_dir / f"year-{task['year']}"
        year_output_dir.mkdir(parents=True, exist_ok=True)
        return SearchProgressStore.build_default_path(
            output_dir=year_output_dir,
            query=task["query"],
            date_from=task["date_from"],
            date_to=task["date_to"],
            max_download=None,
        )

    def _run_single_yearly_export(
        self,
        task: dict[str, Any],
        output_dir: Path,
        progress_file: Path,
    ) -> dict[str, Any]:
        """执行单个年份窗口的导出。"""
        year_output_dir = output_dir / f"year-{task['year']}"
        year_output_dir.mkdir(parents=True, exist_ok=True)
        original_output_dir = self.config.output_dir
        self.config.output_dir = year_output_dir
        try:
            self._open_advanced_search_page()
            if self.ADVANCED_FORM_READY_SELECTORS:
                self._wait_for_any_selector(list(self.ADVANCED_FORM_READY_SELECTORS))
            self._ensure_captcha_cleared()
            result = self.run_advanced_export(
                cli_params=task,
                progress_file=progress_file,
                reuse_current_search_page=True,
            )
        finally:
            self.config.output_dir = original_output_dir
        result["year"] = task["year"]
        return result

    def _write_empty_year_result(self, output_dir: Path, task: dict[str, Any], result: dict[str, Any]) -> str:
        """为无结果年份写入单独说明文件。"""
        year_output_dir = output_dir / f"year-{task['year']}"
        year_output_dir.mkdir(parents=True, exist_ok=True)
        file_path = year_output_dir / f"{task['year']}-no-results.txt"
        lines = [
            f"检索词: {task['query']}",
            "状态: no_results",
            f"年份: {task['year']}",
            f"日期范围: {task.get('date_from') or ''} ~ {task['date_to']}",
            f"URL: {result.get('url', self.page.url)}",
            "说明: 没有命中的记录，请修改条件后重新检索。",
        ]
        file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(file_path)

    def _collect_yearly_validation_outcomes(
        self,
        tasks: list[dict[str, Any]],
        output_dir: Path,
        executed_years: list[str],
        empty_years: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """收集年度校验后的重跑年份与跳过年份。"""
        executed_year_set = set(executed_years)
        empty_year_set = set(empty_years)
        failures: list[dict[str, Any]] = []
        skipped_year_details: list[dict[str, Any]] = []
        for task in tasks:
            year = task["year"]
            if year not in executed_year_set or year in empty_year_set:
                continue
            year_output_dir = output_dir / f"year-{year}"

            if not self._is_year_output_valid(year_output_dir):
                failures.append({
                    "task": task,
                    "year": year,
                    "reason": self._get_year_output_invalid_reason(year_output_dir),
                    "year_output_dir": str(year_output_dir),
                })
                continue

            no_results_note = find_yearly_no_results_note(year_output_dir)
            if no_results_note is not None:
                continue
            report_path = find_latest_yearly_summary_report(year_output_dir)
            merged_excel_path = find_latest_yearly_merged_excel(year_output_dir)
            if report_path is None:
                failures.append({
                    "task": task,
                    "year": year,
                    "reason": "缺少年度汇总报告文件",
                    "year_output_dir": str(year_output_dir),
                })
                continue
            if merged_excel_path is None:
                failures.append({
                    "task": task,
                    "year": year,
                    "reason": "缺少年度合并 Excel 文件",
                    "year_output_dir": str(year_output_dir),
                })
                continue
            try:
                reported_total = read_report_total(report_path)
            except Exception as exc:
                failures.append({
                    "task": task,
                    "year": year,
                    "reason": f"年度汇总报告无法解析: {exc}",
                    "year_output_dir": str(year_output_dir),
                })
                continue
            try:
                actual_rows = count_excel_rows(merged_excel_path)
            except Exception as exc:
                failures.append({
                    "task": task,
                    "year": year,
                    "reason": f"年度合并 Excel 无法读取: {exc}",
                    "year_output_dir": str(year_output_dir),
                })
                continue
            actual_diff = abs(reported_total - actual_rows)
            batch_size = getattr(self, "EXPORT_BATCH_SIZE", 100)
            tolerance = max(10, math.ceil(reported_total / batch_size) * 5)
            if actual_diff > tolerance:
                failures.append(
                    {
                        "task": task,
                        "year": year,
                        "report_path": str(report_path),
                        "merged_excel_path": str(merged_excel_path),
                        "reported_total": reported_total,
                        "actual_rows": actual_rows,
                    }
                )
        return failures, skipped_year_details

    def _is_year_output_valid(self, year_output_dir: Path) -> bool:
        """检查年份输出是否有效。"""
        if not year_output_dir.exists():
            return False
        files = list(year_output_dir.iterdir())
        if not files:
            return False
        return True

    def _get_year_output_invalid_reason(self, year_output_dir: Path) -> str:
        """获取年份输出无效的原因。"""
        if not year_output_dir.exists():
            return "年份文件夹不存在"
        files = list(year_output_dir.iterdir())
        if not files:
            return "年份文件夹为空"
        return "年份输出无效"

    def _rerun_years_until_export_consistent(
        self,
        tasks: list[dict[str, Any]],
        output_dir: Path,
        progress_store: YearlySearchProgressStore,
        search_params: dict[str, Any],
        available_years: list[str],
        executed_years: list[str],
        empty_years: list[str],
        yearly_result_files: list[str],
        batch_report_files: list[str],
        yearly_report_files: list[str],
        empty_result_files: list[str],
        total: int,
        planned_download: int,
        exported_total: int,
        batch_count: int,
        exported_batches: int,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """在总合并前重跑数量不一致的年份（单轮重跑，仍不一致则记录跳过并继续）。"""
        year_index_map = {task["year"]: index for index, task in enumerate(tasks)}
        skipped_year_map: dict[str, dict[str, Any]] = {}
        if hasattr(progress_store, "load"):
            state = progress_store.load()
        else:
            state = {}
        runtime = state.get("runtime", {})
        saved_pending_years = runtime.get("validation_rerun_pending_years", [])

        if saved_pending_years:
            logger.info("从已保存的重跑列表继续: %s", ", ".join(saved_pending_years))
            pending_task_map = {task["year"]: task for task in tasks}
            failures = []
            for year in list(saved_pending_years):
                if year not in pending_task_map:
                    continue
                failures.append({"task": pending_task_map[year], "year": year, "reason": "中断重跑"})
            skipped_year_details = []
        else:
            failures, skipped_year_details = self._collect_yearly_validation_outcomes(
                tasks=tasks, output_dir=output_dir,
                executed_years=executed_years, empty_years=empty_years,
            )
        for item in skipped_year_details:
            skipped_year_map[item["year"]] = item
        if not failures:
            return batch_report_files, list(skipped_year_map.values())

        logger.info(
            "逐年导出结果校验发现数量不一致，开始重跑: %s",
            ", ".join(item["year"] for item in failures),
        )

        for failure in failures:
            task = failure["task"]
            remaining_pending = [y for y in saved_pending_years if y != task["year"]]
            sub_progress_file = self._build_yearly_sub_progress_file(output_dir, task)
            self._rerun_reset_year_for_export(task, output_dir, sub_progress_file)
            self._save_yearly_progress_snapshot(
                progress_store=progress_store,
                status="running",
                search_params=search_params,
                output_dir=output_dir,
                available_years=available_years,
                next_year_index=len(tasks),
                executed_years=executed_years,
                empty_years=empty_years,
                yearly_result_files=yearly_result_files,
                batch_report_files=batch_report_files,
                yearly_report_files=yearly_report_files,
                empty_result_files=empty_result_files,
                current_year=task["year"],
                current_year_date_from=task.get("date_from") or "",
                current_year_date_to=task["date_to"],
                current_year_progress_file=str(sub_progress_file),
                total=total,
                planned_download=planned_download,
                exported_total=exported_total,
                batch_count=batch_count,
                exported_batches=exported_batches,
                final_file_path="",
                validation_rerun_in_progress=True,
                validation_rerun_pending_years=saved_pending_years,
            )
            rerun_result = self._run_single_yearly_export(task, output_dir, sub_progress_file)
            if rerun_result.get("status") == "no_results":
                raise ValidationError(f"年度结果复核重跑后为空: {task['year']}")
            rerun_final = str(rerun_result.get("final_file_path") or "")
            if rerun_final:
                yearly_result_files.append(rerun_final)
            batch_report_files.extend(rerun_result.get("batch_report_files") or [])
            if rerun_result.get("report_file"):
                yearly_report_files.append(str(rerun_result["report_file"]))
            self._save_yearly_progress_snapshot(
                progress_store=progress_store,
                status="running",
                search_params=search_params,
                output_dir=output_dir,
                available_years=available_years,
                next_year_index=len(tasks),
                executed_years=executed_years,
                empty_years=empty_years,
                yearly_result_files=yearly_result_files,
                batch_report_files=batch_report_files,
                yearly_report_files=yearly_report_files,
                empty_result_files=empty_result_files,
                current_year="",
                current_year_date_from="",
                current_year_date_to="",
                current_year_progress_file="",
                total=total,
                planned_download=planned_download,
                exported_total=exported_total,
                batch_count=batch_count,
                exported_batches=exported_batches,
                final_file_path="",
                validation_rerun_in_progress=True,
                validation_rerun_pending_years=remaining_pending,
            )

        # 重跑后再次校验，仍不一致的年份记录跳过
        final_failures, _ = self._collect_yearly_validation_outcomes(
            tasks=tasks, output_dir=output_dir,
            executed_years=executed_years, empty_years=empty_years,
        )
        for f in final_failures:
            year = f["year"]
            diff = abs(f.get("reported_total", 0) - f.get("actual_rows", 0))
            logger.warning("年份 %s 重跑后数据仍不一致（差 %s 条），接受现有结果，继续参与总合并", year, diff)

        return batch_report_files, list(skipped_year_map.values())

    def _rebuild_yearly_output_files(
        self,
        tasks: list[dict[str, Any]],
        output_dir: Path,
        executed_years: list[str],
        empty_years: list[str],
        skipped_years: list[str],
    ) -> tuple[list[str], list[str]]:
        """按任务顺序重建年度合并文件与年度报告列表。"""
        executed_year_set = set(executed_years)
        empty_year_set = set(empty_years)
        skipped_year_set = set(skipped_years)
        yearly_result_files: list[str] = []
        yearly_report_files: list[str] = []
        for task in tasks:
            year = task["year"]
            if year not in executed_year_set or year in empty_year_set or year in skipped_year_set:
                continue
            year_output_dir = output_dir / f"year-{year}"
            report_path = find_latest_yearly_summary_report(year_output_dir)
            merged_excel_path = find_latest_yearly_merged_excel(year_output_dir)
            if report_path is None or merged_excel_path is None:
                logger.warning("年度导出结果文件不完整，已跳过总合并: year=%s", year)
                continue
            yearly_result_files.append(str(merged_excel_path))
            yearly_report_files.append(str(report_path))
        return yearly_result_files, yearly_report_files

    def _build_skipped_year_detail(self, task: dict[str, Any], reason: str) -> dict[str, Any]:
        """构造被跳过年份的记录。"""
        return {
            "year": task["year"],
            "reason": reason,
        }

    def _cleanup_year_output_dir(self, output_dir: Path, task: dict[str, Any]) -> None:
        """重跑年度任务前删除该年度目录下的所有旧文件。"""
        year_output_dir = output_dir / f"year-{task['year']}"
        try:
            cleanup_year_output_dir(year_output_dir)
        except Exception as exc:
            raise ValidationError(f"清理年度输出目录失败: {year_output_dir}") from exc

    def _rerun_reset_year_for_export(self, task: dict[str, Any], output_dir: Path, sub_progress_file: Path) -> None:
        """年份重跑：保留批次文件和进度，从进度续跑。"""
        year_output_dir = output_dir / f"year-{task['year']}"
        if sub_progress_file.exists():
            try:
                data = json.loads(sub_progress_file.read_text(encoding="utf-8"))
                if data.get("status") in ("success", "no_results"):
                    data["status"] = "running"
                    sub_progress_file.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            except Exception:
                pass
        for p in year_output_dir.glob("*-merged.xlsx"):
            p.unlink(missing_ok=True)
        for p in year_output_dir.glob("*-report.txt"):
            if "-batch" not in p.name and not p.name.endswith("-no-results.txt"):
                p.unlink(missing_ok=True)

    def _write_yearly_summary_report(
        self,
        output_dir: Path,
        query: str,
        search_params: dict[str, Any],
        status: str,
        total: int,
        planned_download: int,
        exported_total: int,
        batch_count: int,
        exported_batches: int,
        executed_years: list[str],
        empty_years: list[str],
        skipped_year_details: list[dict[str, Any]],
        final_file_path: str,
        progress_file: str,
    ) -> str:
        """写入逐年模式总报告。"""
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / build_summary_report_output_filename(query=query, fallback="wanfang-yearly")
        lines = [
            f"检索词: {query}",
            f"状态: {status}",
            "模式: yearly",
            f"总数: {total}",
            f"计划导出: {planned_download}",
            f"导出: {exported_total}",
            f"批次数: {exported_batches} / {batch_count}",
        ]
        date_from = search_params.get("date_from")
        date_to = search_params.get("date_to")
        if date_from or date_to:
            lines.append(f"日期范围: {date_from or ''} ~ {date_to or ''}".strip())
        lines.append(f"执行年份: {', '.join(executed_years) if executed_years else '无'}")
        lines.append(f"无结果年份: {', '.join(empty_years) if empty_years else '无'}")
        lines.append(
            "跳过年份: "
            + (
                ", ".join(
                    f"{item['year']}({item['reason']})" for item in skipped_year_details
                )
                if skipped_year_details
                else "无"
            )
        )
        lines.append(f"URL: {self.page.url if self.page else ''}")
        lines.append(f"文件: {final_file_path}")
        lines.append(f"进度文件: {progress_file}")
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(report_path)

    def _save_yearly_progress_snapshot(
        self,
        progress_store: YearlySearchProgressStore,
        status: str,
        search_params: dict[str, Any],
        output_dir: Path,
        available_years: list[str],
        next_year_index: int,
        executed_years: list[str],
        empty_years: list[str],
        yearly_result_files: list[str],
        batch_report_files: list[str],
        yearly_report_files: list[str],
        empty_result_files: list[str],
        current_year: str,
        current_year_date_from: str,
        current_year_date_to: str,
        current_year_progress_file: str,
        total: int,
        planned_download: int,
        exported_total: int,
        batch_count: int,
        exported_batches: int,
        final_file_path: str,
        validation_rerun_in_progress: bool = False,
        validation_rerun_pending_years: Optional[list[str]] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        """写入逐年导出外层进度快照。"""
        state: dict[str, Any] = {
            "version": YearlySearchProgressStore.VERSION,
            "status": status,
            "search_params": {
                "query": str(search_params["query"]).strip(),
                "date_from": search_params.get("date_from"),
                "date_to": search_params.get("date_to"),
            },
            "runtime": {
                "output_dir": str(output_dir.resolve()),
                "available_years": available_years,
                "next_year_index": next_year_index,
                "executed_years": executed_years,
                "empty_years": empty_years,
                "yearly_result_files": yearly_result_files,
                "batch_report_files": batch_report_files,
                "yearly_report_files": yearly_report_files,
                "empty_result_files": empty_result_files,
                "current_year": current_year,
                "current_year_date_from": current_year_date_from,
                "current_year_date_to": current_year_date_to,
                "current_year_progress_file": current_year_progress_file,
                "total": total,
                "planned_download": planned_download,
                "exported_total": exported_total,
                "batch_count": batch_count,
                "exported_batches": exported_batches,
                "final_file_path": final_file_path,
                "validation_rerun_in_progress": validation_rerun_in_progress,
                "validation_rerun_pending_years": validation_rerun_pending_years or [],
            },
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        if error is not None:
            state["last_error"] = {"type": type(error).__name__, "message": str(error)}
        progress_store.save(state)

    def _build_yearly_result_payload(
        self,
        search_params: dict[str, Any],
        output_dir: Path,
        status: str,
        total: int,
        planned_download: int,
        exported_total: int,
        batch_count: int,
        exported_batches: int,
        final_file_path: str,
        report_file: str,
        progress_file: str,
        batch_report_files: list[str],
        intermediate_files: list[str],
        executed_years: list[str],
        empty_years: list[str],
        skipped_year_details: list[dict[str, Any]],
        resumed_from_progress: bool,
    ) -> dict[str, Any]:
        """构造逐年模式统一返回结构。"""
        return {
            "result_type": "advanced_export",
            "status": status,
            "query": str(search_params["query"]).strip(),
            "total": total,
            "selected": exported_total,
            "exported": exported_total,
            "planned_download": planned_download,
            "batch_count": batch_count,
            "exported_batches": exported_batches,
            "core_only": False,
            "date_from": search_params.get("date_from"),
            "date_to": search_params.get("date_to"),
            "date_range": self._format_date_range(search_params.get("date_from"), search_params.get("date_to")),
            "url": self.page.url if self.page else "",
            "file_path": final_file_path,
            "final_file_path": final_file_path,
            "output_dir": str(output_dir),
            "intermediate_files": intermediate_files,
            "batch_report_files": batch_report_files,
            "report_file": report_file,
            "progress_file": progress_file,
            "resumed_from_progress": resumed_from_progress,
            "yearly_mode": True,
            "executed_years": executed_years,
            "empty_years": empty_years,
            "skipped_years": [item["year"] for item in skipped_year_details],
            "skipped_year_details": skipped_year_details,
        }
