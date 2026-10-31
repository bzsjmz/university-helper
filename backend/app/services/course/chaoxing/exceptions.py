import requests


def diagnostic_reason(exc):
    """Safe task-facing reason: exception text can contain URLs and credentials."""
    if isinstance(exc, FontDecodeError):
        return "加密题目字体解码失败，可能缺少字体映射或映射不完整（桌面版可在桌面设置导入后重开应用）"
    if isinstance(exc, requests.Timeout):
        return "网络请求超时"
    if isinstance(exc, requests.ConnectionError):
        return "网络连接失败"
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return f"平台返回 HTTP {status}" if type(status) is int else "平台 HTTP 请求失败"
    if isinstance(exc, requests.RequestException):
        return "网络请求失败"
    if isinstance(exc, RuntimeError) and any(
        text in str(exc).lower() for text in ("can't start new thread", "cannot start new thread")
    ):
        return "线程资源不足"
    if isinstance(exc, (ValueError, KeyError, TypeError, IndexError)):
        return "页面数据解析异常"
    return "处理异常，未能确认完成"


class ChaoxingException(Exception):
    """Base exception for Chaoxing service errors"""


class LoginError(ChaoxingException):
    """Raised when login fails"""


class InputFormatError(ChaoxingException):
    """Raised when input format is invalid"""


class MaxRollBackExceeded(ChaoxingException):
    """Raised when maximum rollback attempts exceeded"""


class MaxRetryExceeded(ChaoxingException):
    """Raised when maximum retry attempts exceeded"""


class FontDecodeError(ChaoxingException):
    """Raised when font decoding fails"""


class PaperNotOpen(ChaoxingException):
    """The platform explicitly says the chapter paper cannot be answered."""


class AuthenticationError(ChaoxingException):
    """Raised when authentication fails"""


class TokenExpiredError(ChaoxingException):
    """Raised when token has expired"""


class InvalidTokenError(ChaoxingException):
    """Raised when token is invalid"""
