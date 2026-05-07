"""万方高级检索进度文件存储。"""

from exceptions import ValidationError
from src.utils.progress_store import BaseSearchProgressStore


class SearchProgressStore(BaseSearchProgressStore):
    """万方高级检索进度文件存储。"""

    SEARCH_PARAM_KEYS = (
        "query",
        "date_from",
        "date_to",
        "max_download",
    )
    BOOLEAN_PARAM_DEFAULTS: dict[str, bool] = {}
    FALLBACK_SLUG = "wanfang"
    VALIDATION_ERROR_CLASS = ValidationError
