"""维普检索相关异常。"""


class VpSearchError(Exception):
    """维普检索基础异常。"""


class BrowserError(VpSearchError):
    """浏览器异常。"""


class ValidationError(VpSearchError):
    """参数校验异常。"""


class CaptchaError(VpSearchError):
    """验证码异常。"""


class TimeoutError(VpSearchError):
    """超时异常。"""


class NavigationStateError(VpSearchError):
    """导航状态异常。"""


class ParseError(VpSearchError):
    """解析异常。"""


class UnsupportedPageError(VpSearchError):
    """页面类型不支持。"""


class ExportProcessingError(VpSearchError):
    """导出结果处理异常。"""
