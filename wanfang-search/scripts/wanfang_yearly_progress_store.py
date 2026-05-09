"""万方截至年份逐年导出进度文件存储。"""

from exceptions import ValidationError
from src.utils.progress_store import BaseSearchProgressStore


class YearlySearchProgressStore(BaseSearchProgressStore):
    """负责读写万方逐年导出外层进度文件。"""

    SEARCH_PARAM_KEYS = (
        "query",
        "date_from",
        "date_to",
    )
    BOOLEAN_PARAM_DEFAULTS: dict[str, bool] = {}
    FALLBACK_SLUG = "wanfang-yearly"
    VALIDATION_ERROR_CLASS = ValidationError
