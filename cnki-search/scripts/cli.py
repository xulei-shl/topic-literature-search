"""CNKI 检索命令行工具。"""

import argparse
import logging
import sys
from pathlib import Path

from browser import BrowserManager
from config import CnkiSearchConfig
from exceptions import CnkiSearchError, ValidationError
from interactor import CnkiSearchInteractor
from src.utils.cli_dates import normalize_date_range, parse_cli_date
from utils import print_human_readable, print_json, save_results, setup_logging

logger = logging.getLogger("cnki_search.cli")


def create_parser() -> argparse.ArgumentParser:
    """创建参数解析器。"""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("-o", "--output-dir", type=Path, help="输出目录")
    parent.add_argument("--headless", action="store_true", help="无头模式运行")
    parent.add_argument("--no-geoip", action="store_true", help="禁用 GeoIP")
    parent.add_argument("--proxy", type=str, help="指定代理地址")
    parent.add_argument("--no-proxy", action="store_true", help="禁用默认代理")
    parent.add_argument("--language", type=str, default="zh-CN", help="浏览器语言，默认 zh-CN")
    parent.add_argument("--no-save", action="store_true", help="不保存 JSON 结果")
    parent.add_argument("--json-only", action="store_true", help="仅输出 JSON")
    parent.add_argument("--debug", action="store_true", help="开启调试日志")

    parser = argparse.ArgumentParser(
        prog="cnki-search",
        description="基于 Camoufox 的 CNKI 检索工具",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login", parents=[parent], help="手动登录并保存会话")

    search_parser = subparsers.add_parser("search", parents=[parent], help="关键词检索")
    search_parser.add_argument("query", help="检索关键词")
    search_parser.add_argument("-n", "--limit", type=int, default=None, help="返回结果数量，默认当前页全部结果")

    advanced_parser = subparsers.add_parser("advanced-search", parents=[parent], help="高级检索")
    advanced_parser.add_argument("--query", "--keyword", dest="query", required=False, help="检索词")
    advanced_parser.add_argument("--date-from", default=None, help="起始年份，支持 YYYY")
    advanced_parser.add_argument("--date-to", default=None, help="结束年份，支持 YYYY")
    advanced_parser.add_argument("--core", action="store_true", help="仅筛选核心来源")
    advanced_parser.add_argument("--include-no-fulltext", action="store_true", help="取消仅看有全文")
    advanced_parser.add_argument("-n", "--max-download", "--max-export", dest="max_download", type=int, default=None, help="最多导出条数")
    advanced_parser.add_argument("--progress-file", type=Path, default=None, help="断点续跑进度文件路径")

    return parser


def build_config(args: argparse.Namespace) -> CnkiSearchConfig:
    """构造配置。"""
    if args.proxy is not None:
        proxy = args.proxy
    elif args.no_proxy:
        proxy = None
    else:
        proxy = None

    return CnkiSearchConfig(
        headless=args.headless,
        geoip=not args.no_geoip,
        proxy=proxy,
        language=args.language,
        output_dir=args.output_dir,
        save_results=not args.no_save,
        json_only=args.json_only,
    )


def run_command(args: argparse.Namespace, config: CnkiSearchConfig) -> dict:
    """执行命令。"""
    browser_manager = BrowserManager(config)
    try:
        page = browser_manager.start()
        interactor = CnkiSearchInteractor(page, config, browser_manager)

        if args.command == "login":
            return interactor.login()

        if args.command == "search":
            return interactor.search(args.query, args.limit)

        if args.command == "advanced-search":
            date_from, date_to = normalize_date_range(args.date_from, args.date_to)
            return interactor.advanced_search(
                query=args.query,
                date_from=date_from,
                date_to=date_to,
                core_only=args.core,
                include_no_fulltext=args.include_no_fulltext,
                max_download=args.max_download,
                progress_file=args.progress_file,
            )

        raise ValidationError(f"不支持的命令: {args.command}")
    finally:
        browser_manager.close()


def main() -> int:
    """CLI 主入口。"""
    parser = create_parser()
    args = parser.parse_args()
    setup_logging("DEBUG" if args.debug else "INFO")

    try:
        config = build_config(args)
        result = run_command(args, config)

        json_path = None
        if config.save_results and args.command != "login":
            output_dir = Path(result["output_dir"]) if result.get("output_dir") else config.ensure_output_dir(result)
            json_path = save_results(result, output_dir)
            result["saved_files"] = {"json": json_path}
            if result.get("file_path"):
                result["saved_files"]["export"] = result["file_path"]
            if result.get("progress_file"):
                result["saved_files"]["progress"] = result["progress_file"]

        if config.json_only:
            print_json(result)
        else:
            print_human_readable(result)
            if json_path:
                print(f"\nJSON: {json_path}")

        return 0
    except ValueError as exc:
        print(f"参数错误: {exc}")
        return 1
    except CnkiSearchError as exc:
        print(f"运行失败: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\n用户中断")
        return 130
    except Exception as exc:
        logger.exception("发生未处理异常: %s", exc)
        print(f"运行失败: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
