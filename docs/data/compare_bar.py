#!/usr/bin/env python3
"""bar_hub 自攒分钟数据 vs Tushare 权威分钟线 交叉核对（一次性验证，非业务功能）。

用法：cd ~/Projects/quant/server && venv/bin/python ../docs/data/compare_bar.py
前置：Tushare stk_mins 限流 1 次/小时（200 积分），限流恢复后跑。
"""
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, "/home/bernard/Projects/quant/server")
from dotenv import load_dotenv
load_dotenv("/home/bernard/Projects/quant/server/.env")

import tushare as ts

SYM_TUSH = "600000.SH"
SYM_HUB = "600000.SHSE"
START, END = "2026-09-01 09:00:00", "2026-09-04 15:00:00"
HUB_CSV = "/home/bernard/Projects/quant/docs/data/bar_hub_600000.csv"


def main():
    # 1. 拉 Tushare 权威分钟线
    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
    tush = pro.stk_mins(ts_code=SYM_TUSH, freq="1min", start_date=START, end_date=END)
    if tush is None or tush.empty:
        print("Tushare 返回空（可能仍限流）")
        sys.exit(1)
    # trade_time 归一到 HH:MM（分钟首标注）
    tush_map = {}
    for _, r in tush.iterrows():
        hhmm = r["trade_time"][11:16]
        tush_map[hhmm] = r

    # 2. 读 bar_hub 自攒数据
    hub_rows = list(csv.DictReader(open(HUB_CSV)))
    hub_by_day = defaultdict(dict)
    for r in hub_rows:
        hhmm = r["ts"][11:16]
        hub_by_day[r["ts"][:10]][hhmm] = r

    # 3. 按天对齐对比（HH:MM 对齐，忽略 Tushare 开盘 09:30 与 hub 收盘 15:01 竞价桶）
    print(f"Tushare {len(tush)} 根 | bar_hub {len(hub_rows)} 根\n")
    for day in sorted(hub_by_day):
        hub = hub_by_day[day]
        matched = 0
        o_diff = h_diff = l_diff = c_diff = 0
        vol_rel = []
        price_mismatch = []
        for hhmm, hr in sorted(hub.items()):
            if hhmm not in tush_map:
                continue
            tr = tush_map[hhmm]
            # 注意 Tushare 列序: close, open, high, low（close 在前）
            to, th, tl, tc = float(tr["open"]), float(tr["high"]), float(tr["low"]), float(tr["close"])
            ho, hh, hl, hc = (float(hr[k]) for k in ("open", "high", "low", "close"))
            matched += 1
            if abs(to - ho) > 0.001: o_diff += 1
            if abs(th - hh) > 0.001: h_diff += 1
            if abs(tl - hl) > 0.001: l_diff += 1
            if abs(tc - hc) > 0.001: c_diff += 1
            if max(abs(to - ho), abs(th - hh), abs(tl - hl), abs(tc - hc)) > 0.011:
                price_mismatch.append((hhmm, to, ho, th, hh, tl, hl, tc, hc))
            tv = float(tr["vol"]); hv = float(hr["volume"])
            if tv > 0 and hv > 0:
                vol_rel.append((tv - hv) / tv)
        print(f"== {day} == 对齐 {matched} 根")
        print(f"  open 不一致: {o_diff} | high: {h_diff} | low: {l_diff} | close: {c_diff}")
        if vol_rel:
            import statistics
            print(f"  volume 相对差 (tushare-hub)/tushare: 中位 {statistics.median(vol_rel)*100:.2f}%  均值 {statistics.mean(vol_rel)*100:.2f}%")
        if price_mismatch:
            print(f"  价格差>1分钱: {len(price_mismatch)} 根")
            for p in price_mismatch[:5]:
                print(f"    {p[0]} open({p[1]}/{p[2]}) high({p[3]}/{p[4]}) low({p[5]}/{p[6]}) close({p[7]}/{p[8]}) [Tushare/hub]")
        # 找出 hub 有但 Tushare 没有的分钟（缺口）
        missing = [hh for hh in sorted(hub) if hh not in tush_map]
        if missing:
            print(f"  hub 有 Tushare 无的分钟: {missing[:10]}")
        print()


if __name__ == "__main__":
    main()
