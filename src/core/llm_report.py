"""LLM 过滤评估的 Markdown 报告生成。"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.llm_filter import LLMResult


def generate_llm_report(
    input_path: Path,
    output_path: Path,
    total_rows: int,
    results: dict[int, LLMResult],
    query: str,
) -> str:
    """生成 LLM 过滤评估的 Markdown 报告并保存。

    Args:
        input_path: 输入文件路径。
        output_path: 输出（过滤后）文件路径。
        total_rows: 总处理行数。
        results: {行索引: LLMResult}。
        query: 检索关键词。

    Returns:
        报告文件路径。
    """
    target_cnt: Counter[str] = Counter()
    level_cnt: Counter[str] = Counter()
    scores = []
    failed_rows: list[tuple[int, str]] = []

    for row_idx in range(total_rows):
        result = results.get(row_idx)
        if result is None or not result.is_success():
            err_msg = result.error if result else "无结果"
            target_cnt["未知(失败)"] += 1
            level_cnt["未知(失败)"] += 1
            failed_rows.append((row_idx + 2, err_msg))
        else:
            target_cnt[str(result.is_target_magazine)] += 1
            level_cnt[result.relevance_level or "未知"] += 1
            if result.relevance_score is not None:
                scores.append(result.relevance_score)

    total_success = total_rows - len(failed_rows)

    def mean(vals: list) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    lines = [
        "# LLM 过滤评估报告",
        "",
        f"- **检索词**: {query}",
        f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        "## 处理概览",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 输入文件 | {input_path} |",
        f"| 输出文件 | {output_path} |",
        f"| 总处理行数 | {total_rows} |",
        f"| 成功 | {total_success} |",
        f"| 失败 | {len(failed_rows)} |",
        f"| 成功率 | {total_success / total_rows * 100:.1f}% |" if total_rows > 0 else "| 成功率 | 0% |",
        "",
        "---",
        "",
        "## 目标期刊分布 (is_target_magazine)",
        "",
        "| 是否目标期刊 | 数量 | 占比 |",
        "|------------|------|------|",
    ]
    for label in ("True", "False", "未知(失败)"):
        count = target_cnt.get(label, 0)
        pct = f"{count / total_rows * 100:.1f}%" if total_rows > 0 else "0%"
        lines.append(f"| {label} | {count} | {pct} |")

    lines += [
        "",
        "## 相关等级分布 (relevance_level)",
        "",
        "| 等级 | 数量 | 占比 |",
        "|------|------|------|",
    ]
    for label in sorted(level_cnt.keys()):
        count = level_cnt[label]
        pct = f"{count / total_rows * 100:.1f}%" if total_rows > 0 else "0%"
        lines.append(f"| {label} | {count} | {pct} |")

    lines += [
        "",
        "## 相关度分数统计 (relevance_score)",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 有效评分行数 | {len(scores)} |",
    ]
    if scores:
        lines += [
            f"| 平均分 | {mean(scores):.1f} |",
            f"| 最高分 | {max(scores)} |",
            f"| 最低分 | {min(scores)} |",
        ]
    else:
        lines.append("| 平均分 | - |")

    if failed_rows:
        lines += [
            "",
            "## 失败详情",
            "",
            "| Excel 行号 | 错误信息 |",
            "|-----------|---------|",
        ]
        for row_idx, err_msg in failed_rows[:50]:
            lines.append(f"| {row_idx} | {err_msg} |")
        if len(failed_rows) > 50:
            lines.append(f"| ... 共 {len(failed_rows)} 行失败，仅显示前 50 条 | |")

    report_path = output_path.with_suffix(".md")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)
