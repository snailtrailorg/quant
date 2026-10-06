"""批 83b 步 0：sync_config 键集完备性钉（配置面 ↔ 代码路由**双向双射**）。

为什么用「双射」而不是「单向包含」：两个方向各是一种真故障——
- **cfg 行有、代码无 handler** → `sync()` 走到 `无 handler 路由` 分支，落 sync_log status=error
  （H-S1：原为 0/0 假 success 且推进游标，即**数据洞**）；用户还能在页面上看到它、触发它。
- **代码有 handler、cfg 无行** → 该同步项在配置面**不存在**：DataManage 看不到、调度器不跑、
  也没法配 schedule/启用——代码认它而 DB 不认（正是批 83b 步 0 修的那个洞：
  `astock_minute`/`astock_minute_5min`/`index_daily` 三键）。

故钉的是 `set(sync_config.id) == set(_HANDLERS) | _VIA_KIND_IDS`——任一侧漂移即红。

DB 依赖：真库直查（无 dev 库自动跳过，惯例同 test_account_permission._db_up）。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _cfgs() -> dict:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, enabled, schedule, trade_day_filter, provider FROM sync_config").fetchall()
    return {r[0]: {"enabled": r[1], "schedule": r[2], "trade_day_filter": r[3], "provider": r[4]}
            for r in rows}


def _kinds() -> set[str]:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        return {r[0] for r in conn.execute("SELECT sync_id FROM sync_kind_config").fetchall()}


class TestDispatchTables:
    """不依赖 DB 的部分：两张路由表自身的一致性。"""

    def test_via_kind_and_handlers_disjoint(self):
        """bar 族静态路由与字面量 handler 表必须互斥（单源路由，防双注册歧义）。

        与 engine.py 模块级 `assert not (_VIA_KIND_IDS & set(_HANDLERS))` 同判据，
        此处从外部再钉一次（engine 那条是 import 期 assert，被 -O 优化掉即失效）。
        """
        from src.data_sync.engine import _HANDLERS, _VIA_KIND_IDS
        assert not (_VIA_KIND_IDS & set(_HANDLERS))

    def test_handlers_count(self):
        """字面量 handler 表：批 83b 收编前 7 + tier1 工厂 7 + 全量重建工厂 2
        + 批 83b 池数据工厂 2（pool_data / pool_data_full_calibrate）+ 批 101 加密 1
        （binance_perp_daily）+ 批 103b 聚宽 A 股日线 1（astock_daily_jq）
        + 批 101b 加密盘中 bar 3（binance_perp_hourly/1min/15min）
        + 批 102b OKX 日线 1（okx_perp_daily）= 24
        （含 0118 收编的 static_symbols/convertible_terms；防工厂回填静默失效）。"""
        from src.data_sync.engine import _HANDLERS
        assert len(_HANDLERS) == 24, sorted(_HANDLERS)


@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestSyncConfigBijection:
    """配置面 ↔ 代码路由双向双射（步 0 的核心判据）。"""

    def test_cfg_ids_equal_dispatch_ids(self):
        from src.data_sync.engine import _HANDLERS, _VIA_KIND_IDS
        cfg = set(_cfgs())
        dispatch = set(_HANDLERS) | set(_VIA_KIND_IDS)
        assert cfg == dispatch, (
            f"配置面有而代码无 handler（会落 error 日志）：{sorted(cfg - dispatch)}；"
            f"代码有 handler 而配置面无行（页面看不到/调度器不跑）：{sorted(dispatch - cfg)}")

    def test_row_count_equals_dispatch_count(self):
        """行数 == 可调度 sync_id 数（17 存量 + 0117 步0 三键 + 0118 收编两条
        + 0119 收编池数据两条 + 批 101 加密 1 + 批 103b 聚宽 1 + 批 101b 盘中 bar 3
        + 批 102b OKX 1 = 30）。

        不写死字面量，而用配置面↔代码面的等式表达——表增长时本钉不必改，只有**不匹配**
        才红；行数字面量另有 test_row_count_arithmetic 守（防"少了一条但两边一起少"）。
        """
        from src.data_sync.engine import _HANDLERS, _VIA_KIND_IDS
        assert len(_cfgs()) == len(set(_HANDLERS) | set(_VIA_KIND_IDS))

    def test_row_count_arithmetic(self):
        """行数量级守门：17 存量 + 3（迁移 0117）+ 2（0118 收编）+ 2（0119 收编）
        + 1（批 101 加密 binance_perp_daily）+ 1（批 103b 聚宽 astock_daily_jq）
        + 3（批 101b 加密盘中 bar）+ 1（批 102b OKX okx_perp_daily）= 30。

        防的是"某条 cfg 行丢了而代码路由也一起丢"（双射仍成立但能力真空）这种同向漂移。
        """
        assert len(_cfgs()) == 30

    def test_via_kind_ids_all_have_kind_rows(self):
        """每个 bar 族 sync_id 必须有 sync_kind_config 归置行——
        否则 `_sync_via_kind` 抛 `sync_kind_config 无归置行`（且该 raise 会走 sync() 的
        except → status=error，不推进游标，属可容忍但仍应零发生）。"""
        missing = set(__import__("src.data_sync.engine", fromlist=["x"])._VIA_KIND_IDS) - _kinds()
        assert not missing, f"缺 sync_kind_config 归置行：{sorted(missing)}"

    def test_filled_three_keys_present_and_schedulable(self):
        """步 0 的三键在位、带全套调度字段、且在代码路由内。"""
        from src.data_sync.engine import _VIA_KIND_IDS
        cfg = _cfgs()
        for sid in ("astock_minute", "astock_minute_5min", "index_daily"):
            assert sid in cfg, f"{sid} 未被迁移 0117 补行"
            assert sid in _VIA_KIND_IDS, f"{sid} 补了行但代码路由里没有——补了也跑不起来"
            row = cfg[sid]
            assert row["schedule"] and row["schedule"] != "manual"
            assert row["trade_day_filter"] in ("none", "workday", "trade_day")
            assert row["provider"] == "tushare"

    def test_minute_keys_stay_disabled(self):
        """两条全市场分钟同步必须保持 enabled=false——迁移 0044 判定其为**风暴源**
        （共用 handler 遍历全静态列表，1 次/分钟限速下打爆配额），由池驱动
        `sync_pools_minute` 替代。

        本钉防的是：步 0 补行时照抄 `init-seed.sql` 的旧字面 `'true'`，把这把火重新点上。
        """
        cfg = _cfgs()
        assert cfg["astock_minute"]["enabled"] is False
        assert cfg["astock_minute_5min"]["enabled"] is False
        assert cfg["index_daily"]["enabled"] is True      # 基准指数无风暴风险，保持启用
