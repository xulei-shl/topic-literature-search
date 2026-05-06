"""维普高级检索进度文件存储。"""

from exceptions import ValidationError
from src.utils.progress_store import BaseSearchProgressStore


class SearchProgressStore(BaseSearchProgressStore):
    """维普高级检索进度文件存储。"""

    SEARCH_PARAM_KEYS = (
        "query",
        "date_from",
        "date_to",
        "core_only",
        "max_download",
    )
    BOOLEAN_PARAM_DEFAULTS = {
        "core_only": False,
    }
    FALLBACK_SLUG = "vp"
    VALIDATION_ERROR_CLASS = ValidationError
