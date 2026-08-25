#!/usr/bin/env python3
"""hub 部署后验证（G2 postverify，批 2 双盲审定稿的形态）。

为什么是"观察生产"而非"影子冒烟实例"：hub 是单实例租约架构（hub:lease fencing），
第二实例会按设计让位退出，且 bar_hub 落库与产重叠——独立冒烟实例在结构上不成立。
故 G2 = 部署后对**真实 hub** 观察数分钟，断言：
  ① 进程稳定：窗口内零 SEGV/Traceback/重启（gen 不变）
  ② 心跳健康：quant:hb:md-hub TTL>0 且 8 个旧字段齐全（超集兼容的运行时证据）
  ③ 数据流动：bar_hub 行数在窗口内持续增长（测试平台 7×24 回放，非交易时段也应增长）

运行（服务器，server/ 目录，root 或 quant）：
  set -a && source .env && set +a && \
  QT_QPA_PLATFORM=offscreen venv/bin/python scripts/run_hub_postverify.py --minutes 5
验收：退出码 0，每周期一行 ✅ 报告。
"""
import argparse
import os
import subprocess
import sys
import time

# 路径 bootstrap（与 run_md_lifecycle 同款——scripts/ 下的脚本 sys.path[0] 是本目录，
# 必须补 server/ 根才能 import src.*；2026-08-25 两次同坑后固化为固定开头）
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRV = os.path.join(_HERE, "..")
if os.path.isdir(os.path.join(_SRV, "src")):
    sys.path.insert(0, _SRV)

UNIT = "quant-md-hub@quant"
HB_KEY = "quant:hb:md-hub"
HUB_FIELDS = ["pid", "gen", "subs", "ticks", "bars", "sess_ticks", "dropped_pg", "last_tick_ts"]


def _redis():
    import redis
    return redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
                                decode_responses=True, socket_timeout=3)


def _bar_count():
    from src.data_platform.db import get_conn
    with get_conn() as c:
        return c.execute("SELECT count(*) FROM bar_hub").fetchone()[0]


def _journal_bad(since: str) -> tuple[int, str]:
    """窗口内硬伤（SEGV/Traceback/dumped）计数 + 摘要。"""
    out = subprocess.run(
        ["journalctl", "-u", UNIT, "--since", since, "--no-pager"],
        capture_output=True, text=True, timeout=15).stdout
    bad = [ln for ln in out.splitlines()
           if "SEGV" in ln or "dumped" in ln or "Traceback" in ln]
    return len(bad), "; ".join(bad[-2:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=5)
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()
    since = "now"   # 脚本启动即窗口起点（部署方应在部署后立即跑）
    r = _redis()
    deadline = time.time() + args.minutes * 60
    gen_first, bars_first, bars_last, fails = None, None, None, 0
    cycle = 0
    while time.time() < deadline:
        cycle += 1
        problems = []
        # ① 进程稳定
        bad_n, bad_tail = _journal_bad(since)
        if bad_n:
            problems.append(f"journal 硬伤×{bad_n}（{bad_tail}）")
        # ② 心跳
        try:
            ttl = r.ttl(HB_KEY)
            fields = list(r.hkeys(HB_KEY))
            gen = r.hget(HB_KEY, "gen")
            if ttl is None or ttl <= 0:
                problems.append(f"心跳键缺失或过期（ttl={ttl}）")
            else:
                missing = [k for k in HUB_FIELDS if k not in fields]
                if missing:
                    problems.append(f"心跳缺旧字段 {missing}（超集破坏）")
            if gen_first is None:
                gen_first, bars_first = gen, _bar_count()
            elif gen != gen_first:
                problems.append(f"gen 变化 {gen_first}->{gen}（窗口内重启）")
        except Exception as e:
            problems.append(f"心跳读失败: {e}")
        # ③ 数据流动
        try:
            bars_now = _bar_count()
            if bars_last is not None and bars_now <= bars_last:
                problems.append(f"bar_hub 未增长（{bars_last}->{bars_now}）")
            bars_last = bars_now
        except Exception as e:
            problems.append(f"bar 计数失败: {e}")
        if problems:
            fails += 1
            print(f"❌ 周期{cycle}: " + " | ".join(problems))
        else:
            print(f"✅ 周期{cycle}: 稳定 | 心跳 TTL/8 字段/gen={gen_first} | bars={bars_now}")
        time.sleep(min(args.interval, max(0, deadline - time.time())))
    print(f"== postverify 结束: {args.minutes} 分钟, 失败周期 {fails}/{cycle} ==")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
