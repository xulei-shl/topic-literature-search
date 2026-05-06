"""CNKI 检索相关异常。"""


class CnkiSearchError(Exception):
    """CNKI 检索基础异常。"""


class BrowserError(CnkiSearchError):
    """浏览器异常。"""


class ValidationError(CnkiSearchError):
    """参数校验异常。"""


class CaptchaError(CnkiSearchError):
    """验证码异常。"""


class TimeoutError(CnkiSearchError):
    """超时异常。"""


class NoResultsError(CnkiSearchError):
    """无结果异常。"""


class ParseError(CnkiSearchError):
    """解析异常。"""


class NavigationStateError(CnkiSearchError):
    """导航状态异常。"""


class UnsupportedPageError(CnkiSearchError):
    """页面类型不支持。"""


class ExportProcessingError(CnkiSearchError):
    """导出结果处理异常。"""
