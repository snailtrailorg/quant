# 批172a 守门红队（B）——只读源码，15 绿实测复现

## ① 防不住的（绕过 → 后果）
**S1**
1. `from vnpy_xtp.gateway.xtp_gateway import XtpTdApi` → `_is_vnpy` 只认 `vnpy`/`vnpy.`，MISSED。**src 现有 5 处漏网**：md_api_guard.py:33、md_gateway.py:202/592/615、td_registry.py:60；D32 L12 明写 `vnpy_xtp` 在边界内 ⇒ 最常挪的包零防护（P0）。
2. `from importlib import import_module as im; im("vnpy.xtp")` / `f=__import__; f("vnpy")` / `name="vnpy"; __import__(name)` / `getattr(importlib,"import_module")("vnpy")` / `exec("import vnpy")` / `__import__(f"vnpy")` / `__import__("vn"+"py")` —— 函数名精确匹配+仅收 Constant 实参，7 种全 MISSED（实测）。
3. 同一行多语句 `import vnpy; x=1` 落在基线已登记 `file:line` 上 → 集合去重后 added/stale 皆空，永不红（实测）：只减不增可按行走廊无限增。
4. 行号漂移令两测试同红，唯一修复路径是「按实际输出重刷基线」=把新 import 顺手洗白（无「条数不增」独立不变量）；且 `test_scanner_sees_a_known_import` 钉死 adapters.py 有 vnpy import，与「P5 后基线为空」矛盾——迁完只能留死 import 或删自检。
**S2**
5. `match p: case "xtp":` —— docstring 明写「允许 match」，实测绿；与 if/elif 等价 ⇒ 给分支留正门（政策缺口）。
6. `X="xtp"; p==X` / `p==f"xtp"` / `p=="x"+"tp"` / `p.startswith("xtp")` / `getattr(x,"xtp")` 全 MISSED（实测；仅相邻字面量 `"x" "tp"` 被解析器折叠才抓到）。
7. 目录改名会 raise（好），但**新增目录**（`ports_v2/`、外置 plugins）永不被扫；词表是有限黑名单，`p=="bond"/"ib"/"swap"` 绿 —— 规则「零分支」，实现「已知名单」。
**S3**
8. register 只校验 `port_caps⊆PORT_CAPS`，不校验 `build`/端口方法：`class NoBuild: manifest={"port_caps":{"execution"}}` 注册+pick 全通过，调用点才 AttributeError（实测）：能力协商只是声明核对。
9. provider 键零校验：`None/123/""/"XTP"/" xtp"` 全接受（实测）；混入非 str 后 `registered()` 排序 TypeError；大小写不归一＝同物两键。
10. 注册后改 manifest 加 `bogus_cap`，pick 照收（每次重读，校验只在注册瞬间）；`pick(impl="legacy")` 与 plugin 返回同一对象，impl 只进报错文案（均实测）。
11. `test_import_registers_and_removal_invalidates` 名为「删包即失效」，实际只调 `clear()`；真删磁盘包不影响活进程注册表（重启才失效），语义未覆盖。
## ② 理论存在但可接受
- S1 看不到 `providers/` 经 PEP562/普通 re-export 外泄（实测 consumer 零命中）：边界本就是「直接 import」，间接依赖由插件包承担。
- `clear()` 无运行时护栏：无生产调用者，docstring 约束足够。
- 同对象重复 register 静默通过：幂等合理；但 `importlib.reload` 新实例 → ValueError（dev 热载炸）且第二次跳过 manifest 复校验 —— 需明确「reload 不支持」。
- `try: import vnpy` 已被 AST 捕获，不构成绕过（纠正直觉）。

## ③ 结论：**须改**
P0＝S1 `_is_vnpy` 补 `vnpy_*` 前缀（现 5 处漏网）+ 基线去重前计数/加条数上限（堵同行走廊与重刷洗白）。P1＝S2 禁 match/case 或纳入扫描 + 词表从 `markets.py` 派生；S3 注册校验 `callable(build)`、provider 为 str 非空，impl 参与选择或删参。其余（删包≠clear、reload）标注语义即可，不阻塞。
