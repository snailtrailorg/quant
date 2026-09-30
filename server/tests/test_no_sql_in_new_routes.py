"""守门测试：路由层 SQL 冻结（体检 2026-09-29 止血，不重构）。

背景：web_api/routes 15 文件内联 269 处 SQL（ast 字符串字面量口径），无 service 层。
本测试锁存量（祖父条款）+ 禁增量：
- 白名单外**新路由文件**：禁 get_conn + 禁 SQL 字面量（SQL 须下沉 data_platform 等层 1-2）
- 白名单内**存量文件**：SQL 计数不超基线（防往存量文件加一句 SELECT 静默通过）

配套：test_layering.py 已断 web_api=层 4 组合根、业务在层 1-2、禁上行 import；
本测试补「路由不许内联 SQL」这一维（layering 只断 import 方向，不断 SQL 字面量）。
"""
import ast
import re
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[1]
_ROUTES_DIR = _SERVER_ROOT / "src" / "web_api" / "routes"

FROZEN_ROUTES = {
    "alerts.py", "auth_routes.py", "backtest.py", "chat.py", "events.py",
    "im_bots.py", "mgmt.py", "quality.py", "risk.py", "routing.py",
    "stock.py", "strategy.py", "sync.py", "system.py", "trading.py",
}

# 存量 SQL 基线（ast 字符串字面量口径，2026-09-29 冻结时点实测；**只许调低不许调高**）
# 2026-09-30 批 83a 下调：mgmt.py 19 → 0（配置面两族 CRUD/拖拽/限流参数/账号权限的 SQL
#   全量下沉 `src/data_platform/config_store.py`（层 1）——该文件本是拆表后「域=表」两族
#   端点的数据访问正位，顺手清掉路由层最后 19 处内联 SQL；本测试不扫 data_platform/，无需登记）。
SQL_BASELINE = {
    "alerts.py": 27, "auth_routes.py": 53, "backtest.py": 27, "chat.py": 14,
    "events.py": 0, "im_bots.py": 14, "mgmt.py": 0, "quality.py": 3,
    "risk.py": 20, "routing.py": 4, "stock.py": 8, "strategy.py": 14,
    "sync.py": 13, "system.py": 25, "trading.py": 28,
}

SQL_RE = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP|TRUNCATE|COPY|"
    r"REFRESH|EXPLAIN|VACUUM|REINDEX|GRANT|REVOKE|BEGIN|COMMIT|ROLLBACK)\b",
    re.I,
)


def _sql_count(src: str) -> int:
    """统计字符串字面量里以 SQL 关键字开头的行数（排除注释/docstring）。

    只走 ast 字符串节点：注释天然跳过；docstring（Expr+Constant）排除；
    f-string 各 Constant 段分别匹配。与冻结基线同一口径。
    """
    tree = ast.parse(src)
    cnt = 0
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue  # docstring
        vals = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            vals = [node.value]
        elif isinstance(node, ast.JoinedStr):
            vals = [v.value for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        for v in vals:
            cnt += sum(1 for line in v.split("\n") if SQL_RE.match(line))
    return cnt


def test_new_route_files_have_no_sql():
    """白名单外的新路由文件禁止 get_conn 与 SQL 字面量（SQL 下沉层 1-2）。"""
    for f in sorted(_ROUTES_DIR.glob("*.py")):
        if f.name in FROZEN_ROUTES or f.name == "__init__.py":
            continue
        src = f.read_text(encoding="utf-8")
        assert "get_conn" not in src, (
            f"{f.name}: 新路由禁止 import/调用 get_conn——SQL 请下沉 "
            f"src/data_platform/<业务>.py（层 1）")
        n = _sql_count(src)
        assert n == 0, (
            f"{f.name}: 新路由禁止内联 SQL（{n} 处）——SQL 请下沉 "
            f"src/data_platform/<业务>.py（层 1）")


def test_frozen_routes_sql_not_growing():
    """存量路由文件的 SQL 计数不超冻结基线（防往老文件加新 SQL 静默通过）。"""
    for f in sorted(_ROUTES_DIR.glob("*.py")):
        if f.name not in FROZEN_ROUTES:
            continue
        n = _sql_count(f.read_text(encoding="utf-8"))
        baseline = SQL_BASELINE[f.name]
        assert n <= baseline, (
            f"{f.name}: SQL 计数 {n} 超基线 {baseline}——存量文件禁止新增 SQL，"
            f"请下沉 src/data_platform/<业务>.py（层 1）")
