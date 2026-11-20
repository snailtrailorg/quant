#!/usr/bin/env python3
"""批189 文档断言门禁（M0 前置）：把「文档里的代码断言」变成可计数项。

用法：
  python3 scripts/verify_b189_docs.py          # 检查，失败 rc=1
  python3 scripts/verify_b189_docs.py --emit   # 同时生成 flow/审查/批189-断言台账.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_REQ = ROOT / 'flow/方案/批189-TD-MD接口拆分-现状与需求.md'
DOC_ARCH = ROOT / 'flow/方案/批189-总体实施架构.md'
DOC_M0 = ROOT / 'flow/方案/批189-M0-契约.md'
LEDGER = ROOT / 'flow/审查/批189-断言台账.md'


def read(p: Path) -> str:
    return p.read_text(encoding='utf-8') if p.exists() else ''


NEED_KEYS = {
    'hub:bars:': 'bars 流（MD code）',
    'hub:latest_tick:': 'tick（MD code + symbol）',
    'hub:latest_tick:pub:': '公共快照（不带 code）',
    'hub:worker:max_ts': 'worker 水位',
    'quant:hb:md-hub': '旧 MD 心跳（退役）',
    'td:cmd:': 'TD 命令队列',
    'td:exec:': '旧 TD 在场键（退役）',
    'risk:cmd:result:': 'TD 回执（含 :final）',
    'iface:hb:': '统一心跳（新）',
}

NEED_CODES = ['MAPPING_MISSING', 'NO_QUOTE', 'IFACE_IDLE', 'CODE_CONFLICT',
              'IFACE_BUSY', 'PROVIDER_UNSUPPORTED', 'CREDENTIALS_INCOMPLETE',
              'PARAM_DEPRECATED']

NEED_ENDPOINTS = ['/api/md-interfaces', '/api/td-interfaces', '/api/data-sources',
                  '/api/trades', '/api/health/components']

NEED_TOKENS = {
    'DOMAIN_TRADING': '域词常量（M8 改名）',
    '0192': 'M1 单文件迁移号',
    'get_interface_row': '读行谓词去 enabled（M3）',
    'legacy_transition': '旧键豁免需删除（M3）',
    '_LegacyMdShim': 'legacy 行情载体（C6 过渡）',
    'run_hub_postverify': 'postverify 读点（M3）',
    'freeze_event': '第 9 处引用（无 FK，需回填）',
}

FORBIDDEN = [
    ('auto_rollback_disabled=true', '不得作为可传开关（playbook 派生）'),
    ('hub:bars:{account_id}', '旧键形状只允许出现在「旧/退役」语境'),
]

# 零容忍（任何语境都不许出现）
FORBIDDEN_ZERO = [
    ('retired_at', 'D11 已改物理删除（2026-11-19 用户裁定）'),
    ('QUOTE_BACKEND_DOWN', '基础设施故障走 503+detail，不进业务码'),
    ('QUOTE_BAD_DATA', '同上'),
]


def ref_exists(rel: str) -> bool:
    bases = (ROOT, ROOT / 'server', ROOT / 'server/src', ROOT / 'server/src/web_api',
             ROOT / 'scripts', ROOT / 'deploy')
    if any((b / rel).exists() for b in bases):
        return True
    target = rel.rsplit('/', 1)[-1]
    for p in ROOT.rglob(target):
        sp = str(p)
        if any(x in sp for x in ('.venv', 'node_modules', '/.git/', '__pycache__')):
            continue
        if sp.endswith(rel):
            return True
    return False


def collect():
    req, arch, m0 = read(DOC_REQ), read(DOC_ARCH), read(DOC_M0)
    both = req + '\n' + arch + '\n' + m0
    rows = []

    for k, why in NEED_KEYS.items():
        rows.append(('键族', k, why, k in both))
    for c in NEED_CODES:
        rows.append(('错误码', c, 'C5/架构 E 表', c in both))
    for e in NEED_ENDPOINTS:
        rows.append(('端点', e, 'API 面', e in both))
    for t, why in NEED_TOKENS.items():
        rows.append(('符号', t, why, t in both))
    rows.append(('权限键', 'system_config', 'D6 两页签共用', 'system_config' in both))

    # 文档引用的 .py 路径必须存在（防文档漂移）
    pat = re.compile(r'((?:[a-z_]+/)*[a-z_0-9]+\.py)(?::\d+)?')
    seen = set()
    for m in pat.finditer(both):
        rel = m.group(1)
        if rel in seen or '/' not in rel:
            continue
        seen.add(rel)
        rows.append(('文件引用', rel, '文档引用的路径须存在', ref_exists(rel)))

    for bad, why in FORBIDDEN_ZERO:
        rows.append(('零容忍', bad, why, bad not in both))

    for bad, why in FORBIDDEN:
        hit = False
        for line in both.splitlines():
            if bad in line and not any(w in line for w in ('旧', '退役', '现状', '迁移前', '原')):
                hit = True
                break
        rows.append(('禁用表述', bad, why, not hit))

    if DOC_M0.exists():
        m = re.search(r'## 6\..*?(?=\n## )', req, re.S)
        block = m.group(0) if m else ''
        ok = ('M0-契约' in block) and ('varchar(32)' not in block) and ('MAPPING_MISSING' not in block)
        rows.append(('M0 指针', '需求 §6 为纯指针', '契约正文只在 M0', ok))
        heads = all(f'## {i}.' in m0 for i in range(1, 8))
        rows.append(('M0 完整', 'C1–C7 七节齐备', 'M0 契约不得缺节', heads))
    return rows


def render(rows) -> str:
    B = chr(96)
    bad = [r for r in rows if not r[3]]
    lines = ['# 批189 断言台账（脚本生成，勿手改）', '',
             f'> 生成：{B}python3 scripts/verify_b189_docs.py --emit{B}；共 {len(rows)} 条，未过 {len(bad)} 条。', '',
             '| 类别 | 断言 | 依据 | 结果 |', '|---|---|---|---|']
    for cat, key, why, ok in rows:
        lines.append(f'| {cat} | {B}{key}{B} | {why} | ' + ('PASS' if ok else '**FAIL**') + ' |')
    return '\n'.join(lines) + '\n'


def emit(rows) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(render(rows), encoding='utf-8')


def main() -> int:
    rows = collect()
    bad = [r for r in rows if not r[3]]
    if '--emit' in sys.argv:
        emit(rows)
    for cat, key, why, ok in rows:
        if not ok:
            print(f'FAIL [{cat}] {key} —— {why}')
    print(f'共 {len(rows)} 条断言，未过 {len(bad)} 条')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
