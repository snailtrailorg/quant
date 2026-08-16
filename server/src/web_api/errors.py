"""统一 API 错误（错误码化，2026-08-15）。

ApiError(status, CODE, message)：响应体 {"detail": message(中文兜底), "code": CODE}。
前端 apiErr(e)：有 err.<CODE> 本地化映射则显示翻译，否则回落 detail——后端不再硬编码语言假设。

加新错误的约定：raise 处定码（UPPER_SNAKE）+ 前端 locales 加 err.<CODE> 各语言条目；
未映射的码自动回落 detail，增量迁移安全。
"""
from fastapi import HTTPException


class ApiError(HTTPException):
    """带错误码的业务异常。code 顶层返回，detail 保持字符串（兼容旧前端）。"""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
