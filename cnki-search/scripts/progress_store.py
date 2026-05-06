"""CNKI 高级检索进度文件存储。"""

from exceptions import ValidationError
from src.utils.progress_store import BaseSearchProgressStore


class SearchProgressStore(BaseSearchProgressStore):
    """CNKI 高级检索进度文件存储。"""

    SEARCH_PARAM_KEYS = (
        "query",
        "date_from",
        "date_to",
        "core_only",
        "include_no_fulltext",
        "max_download",
    )
    BOOLEAN_PARAM_DEFAULTS = {
        "core_only": False,
        "include_no_fulltext": False,
    }
    FALLBACK_SLUG = "cnki"
    VALIDATION_ERROR_CLASS = ValidationError
