"""系统告警读/清（health_event）：当前未消除 + 已消除历史。

判定：每 (rule_id, component) 取最新一行——非 recovery=正在发生；recovery=已消除。
历史卡展示「原始告警 + 对应恢复时间」，清除只删已消除键的全部事件。
"""
from __future__ import annotations

from src.data_platform.db import get_conn


def _row(r) -> dict:
    return {"ts": r[0], "rule_id": r[1], "component": r[2],
            "severity": r[3], "detail": r[4], "resolved_ts": r[5] if len(r) > 5 else None}


def active_alerts() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ts, rule_id, component, severity, detail FROM (
                   SELECT DISTINCT ON (rule_id, component)
                          ts, rule_id, component, severity, detail
                   FROM health_event ORDER BY rule_id, component, ts DESC
               ) latest WHERE severity <> 'recovery' ORDER BY ts DESC""").fetchall()
    return [_row(r) for r in rows]


def resolved_alerts(limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT e.ts, e.rule_id, e.component, e.severity, e.detail, r.ts
               FROM health_event e
               JOIN LATERAL (
                   SELECT ts FROM health_event x
                   WHERE x.rule_id=e.rule_id AND x.component=e.component
                     AND x.severity='recovery' AND x.ts > e.ts
                   ORDER BY x.ts LIMIT 1
               ) r ON true
               WHERE e.severity <> 'recovery'
                 AND NOT EXISTS (   -- 同一故障段（warn→crit→rec）只保留段首告警
                     SELECT 1 FROM health_event p
                     WHERE p.rule_id=e.rule_id AND p.component=e.component
                       AND p.severity <> 'recovery' AND p.ts < e.ts
                       AND NOT EXISTS (
                           SELECT 1 FROM health_event rr
                           WHERE rr.rule_id=e.rule_id AND rr.component=e.component
                             AND rr.severity='recovery' AND rr.ts > p.ts AND rr.ts < e.ts))
               ORDER BY r.ts DESC LIMIT %s""", (int(limit),)).fetchall()
    return [_row(r) for r in rows]


def clear_resolved() -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """DELETE FROM health_event h USING (
                   SELECT DISTINCT ON (rule_id, component) rule_id, component, severity
                   FROM health_event ORDER BY rule_id, component, ts DESC
               ) l
               WHERE h.rule_id=l.rule_id AND h.component=l.component AND l.severity='recovery'""")
        conn.commit()
        return cur.rowcount or 0
