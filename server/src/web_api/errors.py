"""统一 API 错误（错误码化，2026-08-15）。

ApiError(status, CODE, message)：响应体 {"detail": message(中文兜底), "code": CODE}。
前端 apiErr(e)：有 err.<CODE> 本地化映射则显示翻译，否则回落 detail——后端不再硬编码语言假设。

加新错误的约定：raise 处定码（UPPER_SNAKE）+ 前端 locales 加 err.<CODE> 各语言条目；
未映射的码自动回落 detail，增量迁移安全。
"""
from fastapi import HTTPException


class ApiError(HTTPException):
    """带错误码的业务异常。code 顶层返回，detail 保持字符串（兼容旧前端）。

    批12A（B-P2-3）：extra 可选字典——合并进响应体顶层（如 429 携带 existing_ticket
    供前端恢复活会话）；None=零改动。
    批 71：params 可选字典——词条插值参数（响应体顶层 params，前端 apiErr 传
    g.t('err.'+code, params)——词条 {max} 占位与配置值联动，消灭硬编码数字词条）。
    合并顺序立法（v2 #19）：main 先 update(extra) 再写 params——params 为保留键，
    extra 撞名 params=调用方错误。"""

    def __init__(self, status_code: int, code: str, message: str,
                 extra: dict | None = None, params: dict | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.extra = extra
        self.params = params
