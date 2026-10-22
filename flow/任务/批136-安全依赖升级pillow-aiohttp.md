# 批 136 · 安全依赖升级（Pillow 13 / aiohttp 3.14.5）+ 头像解码闸门前移

> 立项 2026-10-21（安全态势页 AI 摘要触发：当日新增 15 条漏洞，pillow 8 条 / aiohttp 3 条）。

## 问题（实核）

安全摘要建议 Pillow → 13.0.0、aiohttp → 3.14.4。实核暴露面：

| 库 | 当前 | 依赖来源 | 暴露面 |
|---|---|---|---|
| Pillow | 12.3.0 | reportlab（PDF 报表）/ weasyprint，**+ 直接用于头像** | **真实**：`auth_routes.py` 头像上传对**用户可控图像** `Image.open` + `load()`（2MB 上限、格式白名单）——解码类 CVE（越界读/堆溢出/DoS）直接对口 |
| aiohttp | 3.14.3 | dingtalk-stream 传递依赖（出站客户端） | 低（客户端场景；连接池耗尽类多为服务端面） |

**环境约束**：Pillow 13.0.0 要求 Python ≥3.11 —— prod=3.11 ✓ / dev=3.10 ✗（装不上）。

## 产出

1. `requirements.txt`：Pillow 按 python_version 分档（≥3.11 → 13.0.0；<3.11 → 12.3.0）+ aiohttp 显式钉 ≥3.14.4（传递依赖防回退）。
2. **头像解码闸门前移**（`auth_routes.py`）：格式白名单校验从 `load()` 之后移到之前——format 由 open 时解析 header 即得，无需先完整解码；原顺序 ⇒ 任意格式先被解码（Pillow 解码 CVE 攻击面）。
3. 回归：头像上传（JPEG/PNG/WEBP + 超限 + 非白名单格式）、PDF 生成（reportlab/weasyprint）、全量 pytest。

## 限定范围
改：`server/requirements.txt`、`server/src/web_api/routes/auth_routes.py`。不碰：其他依赖、图像处理逻辑（裁剪/缩放参数不变）。

## 验收标准
1. dev 全量 pytest 绿（Pillow 12.3 保持；aiohttp 3.14.5）
2. prod 上产后：`pip list` 显示 Pillow 13.x + aiohttp ≥3.14.5；头像上传功能实测（真登录 + 上传一张图）
3. 安全页次日摘要确认两类 CVE 清零/降级

## 风险
- Pillow 12→13 大版本：LANCZOS/crop/resize/save 均验证通过（dev 实跑）；prod 3.11 上首装需回归头像
- reportlab/weasyprint 兼容：本地 PDF 生成测试绿即证
