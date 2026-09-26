#!/usr/bin/env python3
"""旧流回灌（批 66b 一次性迁移工具——D26 键切换暖机代价消解）。

背景：hub 流键裸键 hub:bars:{symbol} → per-account hub:bars:{account_id}:{symbol} 切换后
新流为空（PG bar_1min 已退役无暖机源）→ 不回灌=任务首个交易日零历史起步（240 根≈4h 盲窗）。

动作：SCAN 旧形态键（第二段非纯数字=旧键）→ XREAD 全量条目 → XADD 新键（payload 补
account_id 字段，gen 保留原值——worker gen_jump→rewarm 自动接管）→ 逐键报告。幂等：
--commit 缺省=干跑（只报告不写）；重复执行按 ts 去重天然收敛（worker max_ts 过滤）。

运行（服务器，michael 带外步——批 66b 带外步（§3-7 ④：release 完成后立即跑）；quant 属主能读 .env/venv）：
  cd /data/websites/snailtrail.cc/quant/server && \
  sudo -u quant bash -c 'set -a && source ../shared/.env && set +a && \
  QT_QPA_PLATFORM=offscreen ../shared/venv/bin/python scripts/backfill_hub_bars.py --account-id 1' [--commit]
验收：干跑输出键清单+条目数；--commit 后 hbcheck/worker 暖机日志新流非空。
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRV = os.path.join(_HERE, "..")
if os.path.isdir(os.path.join(_SRV, "src")):
    sys.path.insert(0, _SRV)


def _redis():
    import redis
    return redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
                                decode_responses=True, socket_timeout=5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account-id", type=int, required=True, help="目标账号（接口行 id）")
    ap.add_argument("--commit", action="store_true", help="真写（缺省=干跑只报告）")
    ap.add_argument("--delete-old", action="store_true", help="回灌成功后删旧键（默认保留，人工确认后另行清理）")
    args = ap.parse_args()
    r = _redis()
    moved = 0
    for key in r.scan_iter(match="hub:bars:*", count=500):
        parts = key.split(":")
        # 新形态=hub:bars:{digit}:{symbol}（≥3 段且第二段纯数字）；旧形态=hub:bars:{symbol}（symbol 无冒号）
        if len(parts) >= 3 and parts[1].isdigit():
            continue
        symbol = parts[1]
        target = f"hub:bars:{args.account_id}:{symbol}"
        entries = r.xrange(key, "-", "+")
        print(f"{'✍' if args.commit else '👁'} {key} → {target}（{len(entries)} 条）")
        if args.commit and entries:
            pipe = r.pipeline()
            for eid, fields in entries:
                f2 = dict(fields)
                f2.setdefault("account_id", str(args.account_id))   # payload 契约补齐
                f2["gen"] = "0"   # 盲审 A-P1：gen 置 0——保留旧 gen（≈203）会倒挂（新 hub gen 从 1 起=live bar 永久 stale_gen 拒=静默全盲）；0<一切 live gen，任意投递顺序安全
                pipe.xadd(target, f2, maxlen=5000, approximate=True)
            pipe.execute()
            moved += len(entries)
            if args.delete_old:
                r.delete(key)
    print(f"== 回灌{'完成' if args.commit else '干跑'}: {moved} 条{'（--delete-old：旧键已删）' if args.delete_old and args.commit else ''} ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
