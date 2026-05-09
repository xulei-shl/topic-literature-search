"""万方进度文件测试。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.script_loader import load_script_module

SCRIPT_DIR = ROOT_DIR / "wanfang-search" / "scripts"
_exceptions_module = load_script_module(SCRIPT_DIR, "exceptions", "wanfang_progress_exceptions_module")
_progress_store_module = load_script_module(SCRIPT_DIR, "progress_store", "wanfang_progress_store_module")
_yearly_progress_store_module = load_script_module(
    SCRIPT_DIR,
    "wanfang_yearly_progress_store",
    "wanfang_yearly_progress_store_module",
)

ValidationError = _exceptions_module.ValidationError
SearchProgressStore = _progress_store_module.SearchProgressStore
YearlySearchProgressStore = _yearly_progress_store_module.YearlySearchProgressStore


class SearchProgressStoreTestCase(unittest.TestCase):
    """验证万方进度文件存储与参数恢复逻辑。"""

    def test_save_and_load_progress_state(self) -> None:
        """进度文件应以 UTF-8 JSON 正常读写。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            progress_path = Path(temp_dir) / "progress.json"
            store = SearchProgressStore(progress_path)
            state = {
                "version": 1,
                "status": "running",
                "search_params": {"query": "新青年"},
                "runtime": {"exported_batches": 2},
            }

            store.save(state)
            loaded = store.load()
            raw_text = progress_path.read_text(encoding="utf-8")

        self.assertEqual(loaded, state)
        self.assertEqual(json.loads(raw_text), state)

    def test_resolve_search_params_prefers_progress_file_when_cli_empty(self) -> None:
        """仅传入进度文件时应恢复历史检索参数。"""
        progress_data = {
            "search_params": {
                "query": "新青年",
                "date_from": None,
                "date_to": "2025",
                "max_download": 200,
            }
        }

        resolved = SearchProgressStore.resolve_search_params(
            cli_params={
                "query": None,
                "date_from": None,
                "date_to": None,
                "max_download": None,
            },
            progress_data=progress_data,
        )

        self.assertEqual(resolved["query"], "新青年")
        self.assertEqual(resolved["date_to"], "2025")
        self.assertEqual(resolved["max_download"], 200)

    def test_resolve_search_params_rejects_conflicting_cli_args(self) -> None:
        """CLI 参数与进度文件不一致时应直接报错。"""
        progress_data = {
            "search_params": {
                "query": "新青年",
                "date_from": None,
                "date_to": "2025",
                "max_download": 200,
            }
        }

        with self.assertRaises(ValidationError):
            SearchProgressStore.resolve_search_params(
                cli_params={
                    "query": "鲁迅",
                    "date_from": None,
                    "date_to": "2025",
                    "max_download": 200,
                },
                progress_data=progress_data,
            )

    def test_build_default_path_uses_query_fingerprint(self) -> None:
        """默认进度文件名应包含可识别的检索特征。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            path = SearchProgressStore.build_default_path(
                output_dir=output_dir,
                query="新青年",
                date_from=None,
                date_to="2025",
                max_download=200,
            )

        self.assertEqual(path.parent, output_dir)
        self.assertEqual(path.suffix, ".json")
        self.assertIn("新青年", path.name)

    def test_prepare_store_auto_loads_existing_default_progress_file(self) -> None:
        """未显式传入进度文件时，应自动加载默认进度文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            state = {
                "version": 1,
                "status": "failed",
                "search_params": {"query": "新青年", "date_to": "2025"},
                "runtime": {"exported_batches": 2},
            }
            progress_path = SearchProgressStore.build_default_path(
                output_dir=output_dir,
                query="新青年",
                date_from=None,
                date_to="2025",
                max_download=200,
            )
            progress_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            store, resume_data = SearchProgressStore.prepare_store(
                progress_file=None,
                output_dir=output_dir,
                query="新青年",
                date_from=None,
                date_to="2025",
                max_download=200,
            )

        self.assertEqual(store.file_path, progress_path.resolve())
        self.assertEqual(resume_data, state)


class YearlySearchProgressStoreTestCase(unittest.TestCase):
    """验证万方逐年导出外层进度文件逻辑。"""

    def test_build_default_path_uses_yearly_prefix(self) -> None:
        """逐年导出进度文件应与普通批量导出隔离命名。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            path = YearlySearchProgressStore.build_default_path(
                output_dir=output_dir,
                query="新青年",
                date_from="1949",
                date_to="1980",
            )

        self.assertEqual(path.parent, output_dir)
        self.assertEqual(path.suffix, ".json")
        self.assertIn("progress-", path.name)
        self.assertIn("新青年", path.name)

    def test_resolve_search_params_prefers_progress_file_when_cli_empty(self) -> None:
        """逐年模式恢复执行时应复用进度文件中的检索参数。"""
        progress_data = {
            "search_params": {
                "query": "新青年",
                "date_from": "1949",
                "date_to": "1980",
            }
        }

        resolved = YearlySearchProgressStore.resolve_search_params(
            cli_params={
                "query": None,
                "date_from": None,
                "date_to": None,
            },
            progress_data=progress_data,
        )

        self.assertEqual(resolved["query"], "新青年")
        self.assertEqual(resolved["date_from"], "1949")
        self.assertEqual(resolved["date_to"], "1980")

    def test_resolve_search_params_rejects_conflicting_cli_args(self) -> None:
        """逐年模式的 CLI 参数与进度文件冲突时应直接报错。"""
        progress_data = {
            "search_params": {
                "query": "新青年",
                "date_from": "1949",
                "date_to": "1980",
            }
        }

        with self.assertRaises(ValidationError):
            YearlySearchProgressStore.resolve_search_params(
                cli_params={
                    "query": "鲁迅",
                    "date_from": "1949",
                    "date_to": "1980",
                },
                progress_data=progress_data,
            )


if __name__ == "__main__":
    unittest.main()
