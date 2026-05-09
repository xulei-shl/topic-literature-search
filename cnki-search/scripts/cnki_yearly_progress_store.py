"""CNKI 截至年份逐年导出进度文件存储。"""

from exceptions import ValidationError
from src.utils.progress_store import BaseSearchProgressStore


class YearlySearchProgressStore(BaseSearchProgressStore):
    """负责读写 CNKI 逐年导出外层进度文件。"""

    SEARCH_PARAM_KEYS = (
        "query",
        "date_from",
        "date_to",
        "core_only",
        "include_no_fulltext",
    )
    BOOLEAN_PARAM_DEFAULTS = {
        "core_only": False,
        "include_no_fulltext": False,
    }
    FALLBACK_SLUG = "cnki-yearly"
    VALIDATION_ERROR_CLASS = ValidationError
