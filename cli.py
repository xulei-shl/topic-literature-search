"""顶层高级检索命令行入口。"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import subprocess

PROJECT_ROOT = Path(__file__).resolve().parent
TARGET_SCRIPT_PATHS = {
    "cnki": PROJECT_ROOT / "cnki-search" / "scripts" / "cli.py",
    "vp": PROJECT_ROOT / "vp-search" / "scripts" / "cli.py",
    "wanfang": PROJECT_ROOT / "wanfang-search" / "scripts" / "cli.py",
}
MERGE_EXCEL_SCRIPT = PROJECT_ROOT / "src" / "core" / "merge_excel.py"
TARGET_DISPLAY_NAMES = {
    "cnki": "CNKI",
    "vp": "VP",
    "wanfang": "WANFANG",
}


@dataclass(slots=True)
class DispatchResult:
    """记录单个子任务的执行结果。"""

    target: str
    exit_code: int
    command: list[str]
    error_message: str | None = None


async def run_merge_excel(query: str, targets: list[str]) -> int:
    """执行 Excel 合并任务。

    Args:
        query: 检索关键词。
        targets: 需要合并的数据源列表。

    Returns:
        退出码。
    """
    from src.core.merge_excel import merge_excel_files

    try:
        result_path = merge_excel_files(query, targets)
        print(f"合并完成: {result_path}")
        return 0
    except Exception as exc:
        print(f"合并失败: {exc}")
        return 1


def create_parser() -> argparse.ArgumentParser:
    """创建顶层参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="topic-literature-search",
        description="统一调度多个文献库的高级检索任务",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    advanced_search_parser = subparsers.add_parser("advanced-search", help="并发执行高级检索")
    advanced_search_parser.add_argument("--all", dest="run_all", action="store_true", help="执行全部数据源")
    advanced_search_parser.add_argument("--cnki", action="store_true", help="执行 CNKI")
    advanced_search_parser.add_argument("--vp", action="store_true", help="执行维普")
    advanced_search_parser.add_argument("--wanfang", action="store_true", help="执行万方")

    merge_parser = subparsers.add_parser("merge-excel", help="合并多个数据源的 Excel 文件")
    merge_parser.add_argument("--query", required=True, help="检索关键词，用于定位输出目录")
    merge_parser.add_argument("--all", dest="merge_all", action="store_true", help="合并全部数据源")
    merge_parser.add_argument("--cnki", action="store_true", help="合并 CNKI")
    merge_parser.add_argument("--vp", action="store_true", help="合并维普")
    merge_parser.add_argument("--wanfang", action="store_true", help="合并万方")

    clean_parser = subparsers.add_parser("clean-excel", help="对合并后的 Excel 进行去重")
    clean_parser.add_argument("--query", required=True, help="检索关键词，用于定位合并文件")
    clean_parser.add_argument("--input", help="直接指定输入的合并 Excel 文件路径（可选）")

    llm_parser = subparsers.add_parser("llm-filter", help="使用 LLM 对去重后的 Excel 进行主题相关性评估")
    llm_parser.add_argument("--query", required=True, help="检索关键词，用于定位去重文件")
    llm_parser.add_argument("--input", help="直接指定输入的去重 Excel 文件路径（可选）")
    llm_parser.add_argument("--batch-size", type=int, help="批次大小（可选，优先级高于 .env 配置）")

    return parser


def parse_cli_args(argv: Sequence[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    """解析顶层参数，并保留子命令透传参数。

    Args:
        argv: 命令行参数列表；为空时读取进程参数。

    Returns:
        顶层已解析参数与透传给子脚本的剩余参数列表。
    """
    parser = create_parser()
    return parser.parse_known_args(argv)


def resolve_targets(args: argparse.Namespace) -> list[str]:
    """根据顶层参数解析执行目标。

    Args:
        args: 顶层参数对象。

    Returns:
        需要执行的目标名称列表。

    Raises:
        ValueError: 目标参数组合非法时抛出。
    """
    if args.command == "merge-excel":
        selected_targets = [target for target in ("cnki", "vp", "wanfang") if getattr(args, target)]
        if args.merge_all and selected_targets:
            raise ValueError("--all 不能与 --cnki/--vp/--wanfang 同时使用")
        if args.merge_all:
            return list(TARGET_SCRIPT_PATHS.keys())
        if selected_targets:
            return selected_targets
        raise ValueError("至少需要指定 --all、--cnki、--vp、--wanfang 中的一个")

    selected_targets = [target for target in ("cnki", "vp", "wanfang") if getattr(args, target)]
    if args.run_all and selected_targets:
        raise ValueError("--all 不能与 --cnki/--vp/--wanfang 同时使用")
    if args.run_all:
        return list(TARGET_SCRIPT_PATHS.keys())
    if selected_targets:
        return selected_targets
    raise ValueError("至少需要指定 --all、--cnki、--vp、--wanfang 中的一个")


def build_subprocess_command(target: str, passthrough_args: Sequence[str]) -> list[str]:
    """构造子脚本执行命令。

    Args:
        target: 目标数据源标识。
        passthrough_args: 透传给子脚本的高级检索参数。

    Returns:
        可直接用于启动子进程的命令数组。
    """
    script_path = TARGET_SCRIPT_PATHS[target]
    return [sys.executable, str(script_path), "advanced-search", *passthrough_args]


def build_subprocess_env() -> dict[str, str]:
    """构造子进程环境变量。"""
    env = os.environ.copy()
    project_path = str(PROJECT_ROOT)
    existing_python_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        project_path if not existing_python_path else f"{project_path}{os.pathsep}{existing_python_path}"
    )
    return env


async def run_target_command(target: str, passthrough_args: Sequence[str]) -> DispatchResult:
    """异步执行单个目标脚本。

    Args:
        target: 目标数据源标识。
        passthrough_args: 透传给子脚本的高级检索参数。

    Returns:
        单个目标的执行结果。
    """
    command = build_subprocess_command(target, passthrough_args)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(PROJECT_ROOT),
            env=build_subprocess_env(),
        )
        exit_code = await process.wait()
        return DispatchResult(target=target, exit_code=exit_code, command=command)
    except Exception as exc:
        return DispatchResult(target=target, exit_code=1, command=command, error_message=str(exc))


async def run_selected_targets(targets: Sequence[str], passthrough_args: Sequence[str]) -> list[DispatchResult]:
    """并发执行多个目标脚本。"""
    tasks = [run_target_command(target, passthrough_args) for target in targets]
    return list(await asyncio.gather(*tasks))


def print_summary(results: Sequence[DispatchResult]) -> None:
    """输出任务汇总结果。"""
    success_targets = [TARGET_DISPLAY_NAMES[result.target] for result in results if result.exit_code == 0]
    failed_results = [result for result in results if result.exit_code != 0]

    print("\n执行汇总:")
    print(f"成功: {', '.join(success_targets) if success_targets else '无'}")
    if not failed_results:
        print("失败: 无")
        return

    failed_targets = [TARGET_DISPLAY_NAMES[result.target] for result in failed_results]
    print(f"失败: {', '.join(failed_targets)}")
    for result in failed_results:
        if result.error_message:
            print(f"- {TARGET_DISPLAY_NAMES[result.target]} 启动失败: {result.error_message}")
        else:
            print(f"- {TARGET_DISPLAY_NAMES[result.target]} 退出码: {result.exit_code}")


def main(argv: Sequence[str] | None = None) -> int:
    """顶层 CLI 主入口。"""
    try:
        args, passthrough_args = parse_cli_args(argv)

        if args.command == "merge-excel":
            targets = resolve_targets(args)
            return run_merge_excel(args.query, targets)

        if args.command == "clean-excel":
            from src.core.clean_excel import clean_excel

            input_path = Path(args.input) if args.input else None
            try:
                output_path = clean_excel(args.query, input_path)
                print(f"去重成功: {output_path}")
                return 0
            except Exception as exc:
                print(f"去重失败: {exc}")
                return 1

        if args.command == "llm-filter":
            from src.core.llm_filter import llm_filter

            input_path = Path(args.input) if args.input else None
            try:
                output_path = llm_filter(args.query, input_path, args.batch_size)
                print(f"LLM 评估成功: {output_path}")
                return 0
            except Exception as exc:
                print(f"LLM 评估失败: {exc}")
                return 1

        if args.command != "advanced-search":
            raise ValueError(f"不支持的命令: {args.command}")

        # advanced-search 命令处理
        targets = resolve_targets(args)
        print(f"开始执行 advanced-search，目标: {', '.join(TARGET_DISPLAY_NAMES[target] for target in targets)}")
        results = asyncio.run(run_selected_targets(targets, passthrough_args))
        print_summary(results)
        return 0 if all(result.exit_code == 0 for result in results) else 1

    except ValueError as exc:
        print(f"参数错误: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\n用户中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())