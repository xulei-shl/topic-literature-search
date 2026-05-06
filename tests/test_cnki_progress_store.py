"""CNKI 进度文件测试。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "cnki-search" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from exceptions import ValidationError
from progress_store import SearchProgressStore


class SearchProgressStoreTestCase(unittest.TestCase):
    """验证进度文件存储与参数恢复逻辑。"""

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
                "core_only": False,
                "include_no_fulltext": True,
                "max_download": None,
            }
        }

        resolved = SearchProgressStore.resolve_search_params(
            cli_params={
                "query": None,
                "date_from": None,
                "date_to": None,
                "core_only": False,
                "include_no_fulltext": False,
                "max_download": None,
            },
            progress_data=progress_data,
        )

        self.assertEqual(resolved["query"], "新青年")
        self.assertEqual(resolved["date_to"], "2025")
        self.assertTrue(resolved["include_no_fulltext"])

    def test_resolve_search_params_allows_store_true_without_progress_file(self) -> None:
        """没有进度文件时，布尔参数应直接采用 CLI 值。"""
        resolved = SearchProgressStore.resolve_search_params(
            cli_params={
                "query": "新青年",
                "date_from": None,
                "date_to": "2025",
                "core_only": False,
                "include_no_fulltext": True,
                "max_download": 300,
            },
            progress_data=None,
        )

        self.assertTrue(resolved["include_no_fulltext"])
        self.assertFalse(resolved["core_only"])

    def test_resolve_search_params_rejects_conflicting_cli_args(self) -> None:
        """CLI 参数与进度文件不一致时应直接报错。"""
        progress_data = {
            "search_params": {
                "query": "新青年",
                "date_from": None,
                "date_to": "2025",
                "core_only": False,
                "include_no_fulltext": True,
                "max_download": None,
            }
        }

        with self.assertRaises(ValidationError):
            SearchProgressStore.resolve_search_params(
                cli_params={
                    "query": "鲁迅",
                    "date_from": None,
                    "date_to": "2025",
                    "core_only": False,
                    "include_no_fulltext": True,
                    "max_download": None,
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
                core_only=False,
                include_no_fulltext=True,
                max_download=None,
            )

        self.assertEqual(path.parent, output_dir)
        self.assertEqual(path.suffix, ".json")
        self.assertIn("新青年", path.name)


if __name__ == "__main__":
    unittest.main()
