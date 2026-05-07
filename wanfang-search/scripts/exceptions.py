"""万方检索相关异常。"""


class WanfangSearchError(Exception):
    """万方检索基础异常。"""


class BrowserError(WanfangSearchError):
    """浏览器异常。"""


class ValidationError(WanfangSearchError):
    """参数校验异常。"""


class CaptchaError(WanfangSearchError):
    """验证码异常。"""


class TimeoutError(WanfangSearchError):
    """超时异常。"""


class NoResultsError(WanfangSearchError):
    """无结果异常。"""


class ParseError(WanfangSearchError):
    """解析异常。"""


class NavigationStateError(WanfangSearchError):
    """导航状态异常。"""


class UnsupportedPageError(WanfangSearchError):
    """页面类型不支持。"""


class ExportProcessingError(WanfangSearchError):
    """导出结果处理异常。"""
