"""维普进度文件测试。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.script_loader import load_script_module

SCRIPT_DIR = ROOT_DIR / "vp-search" / "scripts"
_exceptions_module = load_script_module(SCRIPT_DIR, "exceptions", "vp_progress_exceptions_module")
_progress_store_module = load_script_module(SCRIPT_DIR, "progress_store", "vp_progress_store_module")

ValidationError = _exceptions_module.ValidationError
SearchProgressStore = _progress_store_module.SearchProgressStore


class SearchProgressStoreTestCase(unittest.TestCase):
    """验证进度文件存储与参数恢复逻辑。"""

    def test_save_and_load_progress_state(self) -> None:
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
        progress_data = {
            "search_params": {
                "query": "新青年",
                "date_from": None,
                "date_to": "2025",
                "core_only": True,
                "max_download": 100,
            }
        }

        resolved = SearchProgressStore.resolve_search_params(
            cli_params={
                "query": None,
                "date_from": None,
                "date_to": None,
                "core_only": False,
                "max_download": None,
            },
            progress_data=progress_data,
        )

        self.assertEqual(resolved["query"], "新青年")
        self.assertEqual(resolved["date_to"], "2025")
        self.assertTrue(resolved["core_only"])
        self.assertEqual(resolved["max_download"], 100)

    def test_resolve_search_params_allows_store_true_without_progress_file(self) -> None:
        resolved = SearchProgressStore.resolve_search_params(
            cli_params={
                "query": "新青年",
                "date_from": None,
                "date_to": "2025",
                "core_only": True,
                "max_download": 100,
            },
            progress_data=None,
        )

        self.assertTrue(resolved["core_only"])
        self.assertEqual(resolved["max_download"], 100)

    def test_resolve_search_params_rejects_conflicting_cli_args(self) -> None:
        progress_data = {
            "search_params": {
                "query": "新青年",
                "date_from": None,
                "date_to": "2025",
                "core_only": True,
                "max_download": 100,
            }
        }

        with self.assertRaises(ValidationError):
            SearchProgressStore.resolve_search_params(
                cli_params={
                    "query": "鲁迅",
                    "date_from": None,
                    "date_to": "2025",
                    "core_only": True,
                    "max_download": 100,
                },
                progress_data=progress_data,
            )

    def test_build_default_path_uses_query_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            path = SearchProgressStore.build_default_path(
                output_dir=output_dir,
                query="新青年",
                date_from=None,
                date_to="2025",
                core_only=True,
                max_download=100,
            )

        self.assertEqual(path.parent, output_dir)
        self.assertEqual(path.suffix, ".json")
        self.assertIn("新青年", path.name)


if __name__ == "__main__":
    unittest.main()
