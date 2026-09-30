#!/usr/bin/env bash
# 破坏性/改写过的迁移：在 **scratch schema** 里真跑一遍 upgrade + downgrade（真 PG + 在线 alembic）
#
# 为什么需要它（2026-09-30 实证）
#   1. staging 的 DB 版本**可能领先部署代码**（当日实测 staging DB=0121 而部署 release 只到 0115）
#      ⇒ 彩排根本跑不到中间那些迁移的 upgrade；
#   2. downgrade 在**任何**自动化路径里都不执行（`deploy/playbooks/rollback-tasks.yml` 无 alembic：
#      回滚只回代码、schema 一律不后退）。
#   ⇒ 改写迁移后要验证，**这是唯一手段**。判据背景见 `flow/任务/批85-破坏性迁移两步走立法.md`。
#
# 安全：全程只碰 scratch schema（`PGOPTIONS=-c search_path=<scratch>`），`public` 真库一根汗毛不动，
#       且脚本首尾各记一次 `public.alembic_version` 并比对——不一致立即报错退出。
#
# 用法
#   scripts/test-migration-roundtrip.sh              # 跑内置用例（0116 拆表 expand，见「用例区」）
#   KEEP=1 scripts/test-migration-roundtrip.sh       # 跑完保留 scratch schema（人工翻查）
#   SCRATCH=migtest2 scripts/test-migration-roundtrip.sh
#
# 换迁移怎么用：**通用机器不用动**，照「用例区」克隆一份 case —— 改 ① 前置 fixture（迁移前的表形态 +
#   样本行）② FROM/TO 修订号 ③ 断言清单。断言清单是本脚本的价值所在，别只留「upgrade 没报错」。
#
# 连接：默认 127.0.0.1 / quant / quant（本地开发库），可用 PGHOST/PGUSER/PGDATABASE 覆盖。
#   不依赖 .env，不读仓库凭证。
set -uo pipefail

# ─────────────────────────── 配置 ───────────────────────────
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRV="$ROOT/server"
ALEMBIC_BIN="${ALEMBIC_BIN:-$SRV/venv/bin/alembic}"
SCRATCH="${SCRATCH:-migtest}"
KEEP="${KEEP:-0}"

export PGHOST="${PGHOST:-127.0.0.1}"
export PGUSER="${PGUSER:-quant}"
export PGDATABASE="${PGDATABASE:-quant}"
export PGOPTIONS="-c search_path=$SCRATCH"     # ← 隔离关键：迁移 DDL 与 alembic_version 全落 scratch

PSQL="psql -X -q -v ON_ERROR_STOP=1"
FAIL=0
PUB_VER_BEFORE=""

# ────────────────────────── 通用机器 ──────────────────────────
# （这一段与具体迁移无关，换迁移不用改）

chk() {   # chk <描述> <实得> <期望>
  if [ "$2" = "$3" ]; then echo "  ✓ $1: $2"
  else echo "  ✗ $1: 期望[$3] 实得[$2]"; FAIL=1; fi
}
q()   { $PSQL -tA -c "SET search_path = $SCRATCH; $1" 2>&1; }        # 查（钉在 scratch）
x()   { $PSQL -c "SET search_path = $SCRATCH; $1" >/dev/null 2>&1; } # 写（钉在 scratch）
alp() { ( cd "$SRV" && "$ALEMBIC_BIN" "$@" 2>&1 ); }                 # alembic（靠 PGOPTIONS 落 scratch）

precheck() {
  local bad=0
  command -v psql >/dev/null || { echo "✗ 缺 psql"; bad=1; }
  [ -x "$ALEMBIC_BIN" ] || { echo "✗ 找不到 alembic：$ALEMBIC_BIN（可用 ALEMBIC_BIN= 指定）"; bad=1; }
  # 致命护栏：scratch 名必须合法且**绝不是** public（否则 DROP ... CASCADE 会毁真库）
  case "$SCRATCH" in
    public|pg_catalog|information_schema|"") echo "✗ SCRATCH 非法：[$SCRATCH]"; bad=1 ;;
  esac
  [[ "$SCRATCH" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "✗ SCRATCH 须匹配 ^[a-z_][a-z0-9_]*$：[$SCRATCH]"; bad=1; }
  $PSQL -tA -c 'select 1' >/dev/null 2>&1 || { echo "✗ 连不上 PG（$PGUSER@$PGHOST/$PGDATABASE）"; bad=1; }
  [ "$bad" -eq 0 ] || { echo "—— 预检未过，退出 ——"; exit 2; }
  PUB_VER_BEFORE="$($PSQL -tA -c 'select version_num from public.alembic_version' 2>/dev/null)"
  [ -n "$PUB_VER_BEFORE" ] || echo "⚠️  读不到 public.alembic_version（真库未初始化？）——继续，收尾仍会比对"
  echo "预检：PG=$PGUSER@$PGHOST/$PGDATABASE  scratch=$SCRATCH  public 当前版本=[${PUB_VER_BEFORE:-?}]"
}

drop_scratch() { $PSQL -c "DROP SCHEMA IF EXISTS $SCRATCH CASCADE" >/dev/null 2>&1; }
mk_scratch()   { $PSQL -c "CREATE SCHEMA $SCRATCH" >/dev/null 2>&1; }
reset_scratch() { drop_scratch; mk_scratch; }

stamp() { local a; a="$(alp stamp "$1")"; echo "    stamp $1 → $(echo "$a" | tail -1)"; }
step()  { # step <up|down> <target> <方向名>  —— 跑一步并判成败
  local dir="$1" target="$2" label="$3" a
  a="$(alp "$dir"grade "$target")"
  if echo "$a" | grep -qi 'error\|Traceback'; then
    echo "  ✗ $label（$dir $target）失败"; echo "$a" | tail -12; FAIL=1
  else echo "  ✓ $label（$dir $target）执行成功"; fi
}
offline_render() { # offline_render <up|down> <from:to> <outfile> —— 离线渲染（不连库），返回退出码
  ( cd "$SRV" && "$ALEMBIC_BIN" "$1"grade "$2" --sql ) >"$3" 2>"$3.err"
}

finish() {
  echo
  echo "########## 收尾 ##########"
  PUB_VER_AFTER="$($PSQL -tA -c 'select version_num from public.alembic_version' 2>/dev/null)"
  if [ "$PUB_VER_AFTER" = "$PUB_VER_BEFORE" ]; then
    echo "  ✓ public.alembic_version 未变（[${PUB_VER_AFTER:-?}]）——真库未受污染"
  else
    echo "  ✗✗ public.alembic_version 变了：[${PUB_VER_BEFORE:-?}] → [${PUB_VER_AFTER:-?}] —— 立刻人工介入！"
    FAIL=1
  fi
  if [ "$KEEP" = "1" ]; then
    echo "  （KEEP=1）scratch schema [$SCRATCH] 已保留，人工翻查后手动：DROP SCHEMA $SCRATCH CASCADE"
  else
    drop_scratch && echo "  ✓ scratch schema [$SCRATCH] 已删"
  fi
  echo
  if [ "$FAIL" = 0 ]; then echo "=== 全部通过 ==="; else echo "=== 有失败项 ==="; fi
  exit $FAIL
}

# ═════════════════════════ 用例区（换迁移改这里） ═════════════════════════
# 用例：0116 拆表 expand（external_interface → data_source + trading_account），2026-09-30
FROM=0115
TO=0116

# 迁移前的表形态（= 真实 0115 的相关面：主表 + 7 张 account_id FK 子表）+ 样本行。
# 三条样本各有用意：xtp主（多能力，带 account_key/exchanges，迁到 trading_account）、
#                   tushare源（纯数据源，迁到 data_source）、
#                   腾讯僵尸（provider=tencent，迁移会按设计删掉）。
fixture() {
$PSQL <<SQL
SET search_path = $SCRATCH;
CREATE TABLE external_interface (
  id bigserial PRIMARY KEY, name text NOT NULL, provider text NOT NULL, market text NOT NULL,
  exchanges text[], credentials_encrypted text, params jsonb, capabilities text[] NOT NULL,
  position integer NOT NULL DEFAULT 0, enabled boolean DEFAULT true,
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(), account_key text);
CREATE UNIQUE INDEX ix_external_interface_account_key ON external_interface(provider, account_key);
CREATE TABLE live_task (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_live_task_account FOREIGN KEY (account_id) REFERENCES external_interface(id) ON DELETE RESTRICT);
CREATE TABLE position_snapshot (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_position_snapshot_account FOREIGN KEY (account_id) REFERENCES external_interface(id) ON DELETE CASCADE);
CREATE TABLE position_refresh (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_position_refresh_account FOREIGN KEY (account_id) REFERENCES external_interface(id) ON DELETE CASCADE);
CREATE TABLE account_snapshot (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_account_snapshot_account FOREIGN KEY (account_id) REFERENCES external_interface(id) ON DELETE CASCADE);
CREATE TABLE order_log (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_order_log_account FOREIGN KEY (account_id) REFERENCES external_interface(id) ON DELETE SET NULL);
CREATE TABLE trade_log (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_trade_log_account FOREIGN KEY (account_id) REFERENCES external_interface(id) ON DELETE SET NULL);
CREATE TABLE account_permission (account_id bigint PRIMARY KEY,
  CONSTRAINT account_permission_account_id_fkey FOREIGN KEY (account_id) REFERENCES external_interface(id) ON DELETE CASCADE);
INSERT INTO external_interface (name, provider, market, capabilities, exchanges, account_key) VALUES
  ('xtp主','xtp','astock','{trading,rt_quote}','{SSE,SZSE}','8888'),
  ('tushare源','tushare','astock','{hist_quote}',NULL,NULL),
  ('腾讯僵尸','tencent','astock','{rt_quote}',NULL,NULL);
SQL
}

# 7 张子表的 FK 指向（应指向谁，是拆表最易漏的面）
FK_TABLES="'live_task'::regclass,'position_snapshot'::regclass,'position_refresh'::regclass,'account_snapshot'::regclass,'order_log'::regclass,'trade_log'::regclass,'account_permission'::regclass"
fktargets() { q "select coalesce(string_agg(distinct c.confrelid::regclass::text, ','),'(none)') from pg_constraint c where c.contype='f' and c.conrelid in ($FK_TABLES)"; }

# ── 用例 A：正常往返（upgrade → 并存期真实变更 → downgrade 须回得来） ──
case_a() {
echo
echo "########## 用例 A：正常往返回归 ##########"
reset_scratch
fixture
stamp "$FROM"
step up "$TO" "A1 upgrade"

chk "旧表仍在（expand 关键：不许 drop）" "$(q "select to_regclass('external_interface') is not null")" "t"
chk "旧表已删 tencent 僵尸行（2 剩 / 3 原）" "$(q "select count(*) from external_interface")" "2"
chk "新表行数 data_source/trading_account" "$(q "select (select count(*) from data_source)||'/'||(select count(*) from trading_account)")" "1/1"
chk "id 零重映射（交易账号/数据源）" "$(q "select (select id from trading_account)||'/'||(select id from data_source)")" "1/2"
chk "data_source 无 exchanges 列" "$(q "select count(*) from information_schema.columns where table_schema='$SCRATCH' and table_name='data_source' and column_name='exchanges'")" "0"
chk "trading_account 有 exchanges" "$(q "select exchanges::text from trading_account")" "{SSE,SZSE}"
chk "7 张子表 FK 已重指" "$(fktargets)" "trading_account"

echo "-- A2 并存期真实变更：新表改名 + 新增一行数据源"
x "update trading_account set name='xtp主改名' where id=1"
x "insert into data_source (name,provider,market,capabilities) values ('新建源','akshare','astock','{hist_quote}')"
echo "    data_source ids=$(q "select string_agg(id::text,',') from data_source")  （两表独立序列 → 新行将与 trading_account id 撞号）"

step down "$FROM" "A3 downgrade"

chk "旧表行数（2 原 + 1 新）" "$(q "select count(*) from external_interface")" "3"
chk "并存期改名已随真源刷新" "$(q "select name from external_interface where id=1")" "xtp主改名"
chk "新增行已回填" "$(q "select count(*) from external_interface where name='新建源'")" "1"
chk "新表已删 data_source" "$(q "select to_regclass('data_source') is null")" "t"
chk "新表已删 trading_account" "$(q "select to_regclass('trading_account') is null")" "t"
chk "子表 FK 已回退" "$(fktargets)" "external_interface"
}

# ── 用例 B：跨域 id 撞号（降级必须**响亮拒绝**，不许静默丢行） ──
# 背景：拆表后两域各持独立 BIGSERIAL ⇒ 撞号是必然，不是异常。降级若用
# `ON CONFLICT DO NOTHING` 合并回单表 = **静默丢整行账号**（2026-09-30 实修）。
case_b() {
echo
echo "########## 用例 B：跨域 id 撞号（降级须响亮拦停） ##########"
reset_scratch
fixture
stamp "$FROM"
step up "$TO" "B1 upgrade"
x "insert into trading_account (name,provider,market,capabilities,exchanges) values ('新交易账号','xtp','astock','{trading}','{SSE}')"
echo "    data_source ids=$(q "select string_agg(id::text,',') from data_source")  trading_account ids=$(q "select string_agg(id::text,',') from trading_account")  ← 撞号"
a="$(alp downgrade "$FROM")"
if echo "$a" | grep -q '撞号'; then
  echo "  ✓ 降级被响亮拦停"; echo "    $(echo "$a" | grep -m1 '撞号')"
else
  echo "  ✗ 撞号未被拦停（或错误形态不对）"; echo "$a" | tail -8; FAIL=1
fi
chk "被拦停后两新表仍在（未半途残毁）" "$(q "select (to_regclass('data_source') is not null) and (to_regclass('trading_account') is not null)")" "t"
}

# ── 用例 C：离线渲染完整性（不连库，专钉 `op.get_bind()` 截断坑） ──
# 坑：`op.get_bind()` 在 `alembic --sql` 离线模式下会渲成裸 SELECT，且**截断其后全部 SQL**
#     ⇒ upgrade/downgrade 的渲染产物会只剩半截（末尾的 alembic_version 更新与 COMMIT 消失）。
#     迁移里若要用 get_bind() 做断言，必须 `if not context.is_offline_mode():` 短路。
#     判据：渲染产物**必须以版本更新 + COMMIT 收尾**。
case_c() {
echo
echo "########## 用例 C：离线渲染完整性（不连库） ##########"
local tmp; tmp="$(mktemp -d)"
if offline_render up "$FROM:$TO" "$tmp/up.sql"; then
  chk "upgrade --sql 退出码" "0" "0"
else
  echo "  ✗ upgrade --sql 渲染失败"; tail -6 "$tmp/up.sql.err"; FAIL=1
fi
if offline_render down "$TO:$FROM" "$tmp/dn.sql"; then
  chk "downgrade --sql 退出码" "0" "0"
else
  echo "  ✗ downgrade --sql 渲染失败"; tail -6 "$tmp/dn.sql.err"; FAIL=1
fi
chk "upgrade 渲染未被截断（含版本推进）" "$(grep -c "SET version_num='$TO'" "$tmp/up.sql")" "1"
chk "downgrade 渲染未被截断（含版本回退）" "$(grep -c "SET version_num='$FROM'" "$tmp/dn.sql")" "1"
chk "upgrade 渲染以 COMMIT 收尾" "$(tail -2 "$tmp/up.sql" | grep -c '^COMMIT;')" "1"
rm -rf "$tmp"
}

# ═══════════════════════════ 主流程 ═══════════════════════════
echo "########## 迁移往返回归（scratch=$SCRATCH  revisions=$FROM→$TO） ##########"
precheck
case_a
case_b
case_c
finish
