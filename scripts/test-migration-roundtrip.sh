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

# ── 用例 D：0122 contract（两步走收口步：DROP 旧表），2026-10-01 ──
# 前置 = 0116 expand 后的**并存态**（0121）：两新表为真源 + 旧表留存（停止写入的陈旧快照）
# + 7 子表 FK 已重指 trading_account。旧表里故意留一条「并存期被删账号」的陈旧行（id=3）——
# 验证「重建以新表为真源」：陈旧行不得复活。
FROM_D=0121
TO_D=0122

fixture_contract() {
$PSQL <<SQL
SET search_path = $SCRATCH;
CREATE TABLE data_source (
  id bigserial PRIMARY KEY, name text NOT NULL, provider text NOT NULL, market text NOT NULL,
  credentials_encrypted text, params jsonb, capabilities text[] NOT NULL,
  position integer NOT NULL DEFAULT 0, enabled boolean DEFAULT true,
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now());
CREATE TABLE trading_account (
  id bigserial PRIMARY KEY, name text NOT NULL, provider text NOT NULL, market text NOT NULL,
  exchanges text[], account_key text, credentials_encrypted text, params jsonb,
  capabilities text[] NOT NULL, position integer NOT NULL DEFAULT 0, enabled boolean DEFAULT true,
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now());
CREATE UNIQUE INDEX ix_trading_account_account_key ON trading_account(provider, account_key);
CREATE TABLE external_interface (
  id bigserial PRIMARY KEY, name text NOT NULL, provider text NOT NULL, market text NOT NULL,
  exchanges text[], credentials_encrypted text, params jsonb, capabilities text[] NOT NULL,
  position integer NOT NULL DEFAULT 0, enabled boolean DEFAULT true,
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(), account_key text);
CREATE UNIQUE INDEX ix_external_interface_account_key ON external_interface(provider, account_key);
CREATE TABLE live_task (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_live_task_account FOREIGN KEY (account_id) REFERENCES trading_account(id) ON DELETE RESTRICT);
CREATE TABLE position_snapshot (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_position_snapshot_account FOREIGN KEY (account_id) REFERENCES trading_account(id) ON DELETE CASCADE);
CREATE TABLE position_refresh (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_position_refresh_account FOREIGN KEY (account_id) REFERENCES trading_account(id) ON DELETE CASCADE);
CREATE TABLE account_snapshot (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_account_snapshot_account FOREIGN KEY (account_id) REFERENCES trading_account(id) ON DELETE CASCADE);
CREATE TABLE order_log (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_order_log_account FOREIGN KEY (account_id) REFERENCES trading_account(id) ON DELETE SET NULL);
CREATE TABLE trade_log (id bigserial PRIMARY KEY, account_id bigint,
  CONSTRAINT fk_trade_log_account FOREIGN KEY (account_id) REFERENCES trading_account(id) ON DELETE SET NULL);
CREATE TABLE account_permission (account_id bigint PRIMARY KEY,
  CONSTRAINT account_permission_account_id_fkey FOREIGN KEY (account_id) REFERENCES trading_account(id) ON DELETE CASCADE);
INSERT INTO trading_account (id, name, provider, market, capabilities, exchanges, account_key) VALUES
  (1, 'xtp主','xtp','astock','{trading,rt_quote}','{SSE,SZSE}','8888');
INSERT INTO data_source (id, name, provider, market, capabilities) VALUES
  (2, 'tushare源','tushare','astock','{hist_quote}');
-- 旧表 = 0116 搬迁时刻的陈旧快照 + 一条「并存期被删账号」（id=3，新表里已无此行）
INSERT INTO external_interface (id, name, provider, market, capabilities, exchanges, account_key) VALUES
  (1, 'xtp主','xtp','astock','{trading,rt_quote}','{SSE,SZSE}','8888'),
  (2, 'tushare源','tushare','astock','{hist_quote}',NULL,NULL),
  (3, '已删账号','xtp','astock','{trading}','{SSE}','9999');
-- 序列复位（对齐 0116 upgrade 的真实后置态：显式 id 插入后必须 setval，
-- 否则用例 E 的自增插入会 nextval=1 撞自家主键、静默失败，造不出撞号）
SELECT setval(pg_get_serial_sequence('trading_account','id'), (SELECT max(id) FROM trading_account));
SELECT setval(pg_get_serial_sequence('data_source','id'),    (SELECT max(id) FROM data_source));
SQL
}

case_d() {
echo
echo "########## 用例 D：0122 contract（并存 → 旧表消失 → 降级重建） ##########"
reset_scratch
fixture_contract
stamp "$FROM_D"
step up "$TO_D" "D1 upgrade（DROP 旧表）"

chk "旧表已消失（contract 关键）" "$(q "select to_regclass('external_interface') is null")" "t"
chk "两新表仍在（真源不动）" "$(q "select (to_regclass('data_source') is not null) and (to_regclass('trading_account') is not null)")" "t"
chk "子表 FK 不受 0122 影响" "$(fktargets)" "trading_account"

step down "$FROM_D" "D2 downgrade（重建旧表）"

chk "旧表已重建" "$(q "select to_regclass('external_interface') is not null")" "t"
chk "行数=新表合并（陈旧行 id=3 不复活）" "$(q "select count(*) from external_interface")" "2"
chk "陈旧行确未复活" "$(q "select count(*) from external_interface where id=3")" "0"
chk "交易行整行回填（exchanges/account_key）" "$(q "select exchanges::text||'/'||account_key from external_interface where id=1")" "{SSE,SZSE}/8888"
chk "数据源行 exchanges/account_key 为 NULL" "$(q "select exchanges is null and account_key is null from external_interface where id=2")" "t"
chk "重建含 0099 唯一索引" "$(q "select count(*) from pg_indexes where schemaname='$SCRATCH' and tablename='external_interface' and indexname='ix_external_interface_account_key'")" "1"
chk "子表 FK 仍指 trading_account（0122 不动 FK）" "$(fktargets)" "trading_account"
chk "两新表仍在" "$(q "select (to_regclass('data_source') is not null) and (to_regclass('trading_account') is not null)")" "t"
}

# ── 用例 E：contract 降级的跨域撞号拦停（同 0116 的 B 用例，钉 0122 的 downgrade 断言） ──
case_e() {
echo
echo "########## 用例 E：contract 降级撞号（须响亮拦停） ##########"
reset_scratch
fixture_contract
stamp "$FROM_D"
step up "$TO_D" "E1 upgrade"
x "insert into trading_account (name,provider,market,capabilities,exchanges) values ('新交易账号','xtp','astock','{trading}','{SSE}')"
echo "    trading_account ids=$(q "select string_agg(id::text,',') from trading_account")  data_source ids=$(q "select string_agg(id::text,',') from data_source")  ← 撞号"
a="$(alp downgrade "$FROM_D")"
if echo "$a" | grep -q '撞号'; then
  echo "  ✓ 降级被响亮拦停"; echo "    $(echo "$a" | grep -m1 '撞号')"
else
  echo "  ✗ 撞号未被拦停（或错误形态不对）"; echo "$a" | tail -8; FAIL=1
fi
chk "被拦停后旧表未半建" "$(q "select to_regclass('external_interface') is null")" "t"
}

# ── 用例 F：0122 离线渲染完整性（不连库） ──
case_f() {
echo
echo "########## 用例 F：0122 离线渲染完整性（不连库） ##########"
local tmp; tmp="$(mktemp -d)"
if offline_render up "$FROM_D:$TO_D" "$tmp/up.sql"; then
  chk "upgrade --sql 退出码" "0" "0"
else
  echo "  ✗ upgrade --sql 渲染失败"; tail -6 "$tmp/up.sql.err"; FAIL=1
fi
if offline_render down "$TO_D:$FROM_D" "$tmp/dn.sql"; then
  chk "downgrade --sql 退出码" "0" "0"
else
  echo "  ✗ downgrade --sql 渲染失败"; tail -6 "$tmp/dn.sql.err"; FAIL=1
fi
chk "upgrade 渲染未被截断（含版本推进）" "$(grep -c "SET version_num='$TO_D'" "$tmp/up.sql")" "1"
chk "downgrade 渲染未被截断（含版本回退）" "$(grep -c "SET version_num='$FROM_D'" "$tmp/dn.sql")" "1"
chk "upgrade 渲染含 DROP TABLE IF EXISTS" "$(grep -c 'DROP TABLE IF EXISTS external_interface' "$tmp/up.sql")" "1"
rm -rf "$tmp"
}

# ── 用例 G：0123 freeze_event（纯 expand 建表 + 幂等 seed），2026-10-01 批 76 ──
# 前置 = 0122 contract 之后。0123 **无破坏性 DDL**（只 CREATE TABLE + CREATE INDEX +
# INSERT ON CONFLICT DO NOTHING）⇒ 不需要 allow_contract，但 downgrade 有两条真断言：
#   ① drop_table freeze_event 须干净回收；
#   ② `DELETE FROM system_config WHERE key='web_base_url'` —— 依赖 system_config 表在
#      **scratch 里也必须存在**（stamp 到空 schema 时该表不存在，DELETE 会炸）。
#      这正是本用例的价值：把「降级依赖前序表」这个隐式前提显式钉住。
FROM_E=0122
TO_E=0123

fixture_freeze() {
$PSQL <<SQL
SET search_path = $SCRATCH;
-- 前序面：system_config（0123 downgrade 的 DELETE 目标）+ freeze_event 的引用面 live_task
CREATE TABLE system_config (
  key text PRIMARY KEY, value text, value_type text, description text,
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now());
CREATE TABLE live_task (id bigserial PRIMARY KEY, symbol text, status text);
-- 预置一个「运维已填值」的 web_base_url —— 验幂等 seed 不覆盖（真上产场景）
INSERT INTO system_config (key, value, value_type, description)
  VALUES ('web_base_url', 'https://quant.snailtrail.cc', 'text', '运维预填')
  ON CONFLICT (key) DO NOTHING;
-- 一条无关键，验降级只删 web_base_url、不动邻居
INSERT INTO system_config (key, value, value_type, description)
  VALUES ('unrelated_key', 'keepme', 'text', 'downgrade 不得误删') ON CONFLICT (key) DO NOTHING;
SQL
}

case_g() {
echo
echo "########## 用例 G：0123 freeze_event（建表 + seed 幂等 + 降级回收） ##########"
reset_scratch
fixture_freeze
stamp "$FROM_E"

# --- G1 upgrade ---
step up "$TO_E" "G1 upgrade（建 freeze_event + seed）"
chk "freeze_event 表已在位" "$(q "select to_regclass('freeze_event') is not null" )" "t"
chk "进行中事件部分索引在位" "$(q "select count(*) from pg_indexes where schemaname='$SCRATCH' and indexname='ix_freeze_event_open'")" "1"
chk "时间线索引在位" "$(q "select count(*) from pg_indexes where schemaname='$SCRATCH' and indexname='ix_freeze_event_task_frozen_at'")" "1"
# scene：四 CHECK 里最易写错的两条——闭环一致性 + jsonb 守卫，各打一枪
chk "CHECK 闭环一致性拒绝「解了无方法」" \
  "$(q "insert into freeze_event (task_id,symbol,freeze_type,unfrozen_at) values (1,'X.SH','ts_gap',now())" | grep -c 'ck_freeze_event_close_consistent')" "1"
chk "CHECK jsonb 守卫拒绝非 object" \
  "$(q "insert into freeze_event (task_id,symbol,freeze_type,detail) values (2,'X.SH','ts_gap','[1,2]'::jsonb)" | grep -c 'ck_freeze_event_detail_jsonb')" "1"
chk "CHECK 冻结类型枚举拒绝非法值" \
  "$(q "insert into freeze_event (task_id,symbol,freeze_type) values (3,'X.SH','bogus')" | grep -c 'ck_freeze_event_type')" "1"
# 正向：一条合法行须能落（防「全拒绝」假绿）
x "insert into freeze_event (task_id,symbol,freeze_type,detail) values (9,'510300.SHSE','seq_gap','{\"gap\":65}'::jsonb)"
chk "合法行可插入" "$(q "select count(*) from freeze_event where task_id=9")" "1"
# seed 幂等：预填的运维值**不得被覆盖**（ON CONFLICT DO NOTHING 的关键语义）
chk "seed 不覆盖已填的 web_base_url" \
  "$(q "select value from system_config where key='web_base_url'")" "https://quant.snailtrail.cc"

# --- G2 downgrade ---
step down "$FROM_E" "G2 downgrade（删表 + 回收键）"
chk "freeze_event 表已消失" "$(q "select to_regclass('freeze_event') is null")" "t"
chk "web_base_url 键已回收" "$(q "select count(*) from system_config where key='web_base_url'")" "0"
chk "无关键未被误删" "$(q "select value from system_config where key='unrelated_key'")" "keepme"
}

# ── 用例 H：0123 离线渲染完整性（不连库） ──
case_h() {
echo
echo "########## 用例 H：0123 离线渲染完整性（不连库） ##########"
local tmp; tmp="$(mktemp -d)"
if offline_render up "$FROM_E:$TO_E" "$tmp/up.sql"; then
  chk "upgrade --sql 退出码" "0" "0"
else
  echo "  ✗ upgrade --sql 渲染失败"; tail -6 "$tmp/up.sql.err"; FAIL=1
fi
if offline_render down "$TO_E:$FROM_E" "$tmp/dn.sql"; then
  chk "downgrade --sql 退出码" "0" "0"
else
  echo "  ✗ downgrade --sql 渲染失败"; tail -6 "$tmp/dn.sql.err"; FAIL=1
fi
chk "upgrade 渲染未被截断（含版本推进）" "$(grep -c "SET version_num='$TO_E'" "$tmp/up.sql")" "1"
chk "downgrade 渲染未被截断（含版本回退）" "$(grep -c "SET version_num='$FROM_E'" "$tmp/dn.sql")" "1"
    chk "upgrade 渲染含建表" "$(grep -c 'CREATE TABLE freeze_event' "$tmp/up.sql")" "1"
chk "upgrade 渲染含 seed" "$(grep -c "VALUES ('web_base_url'" "$tmp/up.sql")" "1"
rm -rf "$tmp"
}

# ── 用例 I：0124 补种 live_control（纯 permit 表 DML，无 DDL），2026-10-01 批 76b ──
# 前置 = 0123 之后。0124 **零 DDL**（只 INSERT/DELETE permission 行）⇒ 无 allow_contract。
# 本用例的价值全在**语义断言**（不是「upgrade 没报错」）：
#   ① 幂等：permission 的 UNIQUE 约束 + ON CONFLICT DO NOTHING ⇒ **重跑不报错、不重复**；
#   ② **不覆盖用户态**：用户若已把 live_control 设成 **deny**（或已在界面勾成 allow），
#      迁移**不得**翻转其 effect（这是「补种」与「强写」的分界——补种只在缺行时落行）；
#   ③ 边界：**只碰 live_control**，不得误动 halt/live_trading_control（批 77 续的 admin 独占裁定）；
#   ④ 降级回收：删 live_control 行 + **不误删邻居行**。
FROM_I=0123
TO_I=0124

fixture_liveperm() {
$PSQL <<SQL
SET search_path = $SCRATCH;
CREATE TABLE permission (
  id BIGSERIAL PRIMARY KEY,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  dimension TEXT NOT NULL DEFAULT 'api',
  resource TEXT NOT NULL,
  effect TEXT NOT NULL DEFAULT 'allow',
  note TEXT, updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (subject_type, subject_id, dimension, resource, effect));
-- 复刻 prod 形状：四角色 api 行（0056 seed）+ market_op 行（0074）+ admin alerts_config（0061）。
-- 关键：**故意都不含 live_control**——这就是 P0 现场。
INSERT INTO permission (subject_type,subject_id,dimension,resource,effect,note) VALUES
  ('role','viewer','api','read','allow','0056 seed'),
  ('role','analyst','api','read','allow','0056 seed'),
  ('role','analyst','api','strategy_control','allow','0056 seed'),
  ('role','analyst','api','data_sync','allow','0056 seed'),
  ('role','trader','api','read','allow','0056 seed'),
  ('role','trader','api','strategy_control','allow','0056 seed'),
  ('role','trader','api','halt','allow','0056 seed'),
  ('role','trader','api','trade','allow','0056 seed'),
  ('role','trader','api','live_trading_control','allow','0056 seed'),
  ('role','admin','api','read','allow','0056 seed'),
  ('role','admin','api','halt','allow','0056 seed'),
  ('role','admin','api','live_trading_control','allow','0056 seed'),
  ('role','admin','api','alerts_config','allow','0061 seed'),
  ('role','admin','market_op','astock','allow','0074 seed'),
  ('role','trader','market_op','astock','allow','0074 seed');
-- 预置一条**用户态 deny**（模拟「运维已在界面显式拒绝某组用实盘面」）：
-- 用在 viewer 上（本迁移不碰 viewer）——同时可验「迁移只写 admin/trader」。
INSERT INTO permission (subject_type,subject_id,dimension,resource,effect,note)
  VALUES ('role','viewer','api','trade','deny','运维显式拒绝');
SQL
}

case_i() {
echo
echo "########## 用例 I：0124 补种 live_control（幂等 + 不覆盖 + 边界 + 回收） ##########"
reset_scratch
fixture_liveperm
stamp "$FROM_I"

# --- I1 upgrade ---
step up "$TO_I" "I1 upgrade（补种 admin/trader 的 live_control）"
chk "admin 已获 live_control" \
  "$(q "select count(*) from permission where subject_type='role' and subject_id='admin' and dimension='api' and resource='live_control' and effect='allow'")" "1"
chk "trader 已获 live_control" \
  "$(q "select count(*) from permission where subject_type='role' and subject_id='trader' and dimension='api' and resource='live_control' and effect='allow'")" "1"
# 原则守卫（批 77 §一）：analyst/viewer **不得**被补种——本迁移是「补漏」不是「扩权」
chk "analyst 未被补种（零 live_control 行）" \
  "$(q "select count(*) from permission where subject_id='analyst' and resource='live_control'")" "0"
chk "viewer 未被补种（零 live_control 行）" \
  "$(q "select count(*) from permission where subject_id='viewer' and resource='live_control'")" "0"
# 边界：只碰 live_control，halt/live_trading_control 行数不变（批 77 续 admin 独占裁定不受扰）
chk "halt 行数未变（2: admin+trader）" \
  "$(q "select count(*) from permission where resource='halt'")" "2"
chk "live_trading_control 行数未变（2）" \
  "$(q "select count(*) from permission where resource='live_trading_control'")" "2"
# 用户态不被覆盖：viewer 的显式 deny 行仍在（迁移不得清整表）
chk "用户显式 deny 行未被清" \
  "$(q "select count(*) from permission where subject_id='viewer' and resource='trade' and effect='deny'")" "1"
chk "market_op 维不受扰（2 行）" \
  "$(q "select count(*) from permission where dimension='market_op'")" "2"
chk "总行数 = 16 + 2（只多两行）" "$(q "select count(*) from permission")" "18"

# --- I2 幂等复跑（关键：真上产会 upgrade 两次/与 UI 勾选并发） ---
a="$(alp upgrade "$TO_I")"
if echo "$a" | grep -qi 'error\|Traceback'; then
  echo "  ✗ 复跑 upgrade 报错（幂等性破了）"; echo "$a" | tail -6; FAIL=1
else
  echo "  ✓ 复跑 upgrade 无错（ON CONFLICT DO NOTHING 生效）"
fi
chk "复跑后 admin 仍恰好 1 行（未重复插）" \
  "$(q "select count(*) from permission where subject_id='admin' and resource='live_control'")" "1"
chk "复跑后总行数不变（仍 18）" "$(q "select count(*) from permission")" "18"

# --- I3 模拟「用户已在界面勾选」（迁移前已有 allow 行）⇒ 迁移须为 no-op 且不覆盖 note ---
x "update permission set note='用户手工勾选' where subject_id='admin' and resource='live_control'"
b="$(alp upgrade "$TO_I")"
if echo "$b" | grep -qi 'error\|Traceback'; then
  echo "  ✗ 有行时复跑报错"; echo "$b" | tail -6; FAIL=1
else
  echo "  ✓ 已有行时复跑无错（no-op）"
fi
chk "既有行的 note 未被迁移覆写（真 no-op）" \
  "$(q "select note from permission where subject_id='admin' and resource='live_control'")" "用户手工勾选"

# --- I4 downgrade ---
step down "$FROM_I" "I4 downgrade（回收 live_control 行）"
chk "live_control 行已全清" \
  "$(q "select count(*) from permission where resource='live_control'")" "0"
chk "admin 其余行未被误删" \
  "$(q "select count(*) from permission where subject_id='admin' and dimension='api'")" "4"
chk "trader 其余行未被误删" \
  "$(q "select count(*) from permission where subject_id='trader' and dimension='api'")" "5"
chk "用户显式 deny 行仍在（降级不碰无关行）" \
  "$(q "select count(*) from permission where subject_id='viewer' and resource='trade' and effect='deny'")" "1"
chk "market_op 维未动（2 行）" \
  "$(q "select count(*) from permission where dimension='market_op'")" "2"
chk "总行数回到 16" "$(q "select count(*) from permission")" "16"
}

# ── 用例 J：0124 离线渲染完整性（不连库） ──
case_j() {
echo
echo "########## 用例 J：0124 离线渲染完整性（不连库） ##########"
local tmp; tmp="$(mktemp -d)"
if offline_render up "$FROM_I:$TO_I" "$tmp/up.sql"; then
  chk "upgrade --sql 退出码" "0" "0"
else
  echo "  ✗ upgrade --sql 渲染失败"; tail -6 "$tmp/up.sql.err"; FAIL=1
fi
if offline_render down "$TO_I:$FROM_I" "$tmp/dn.sql"; then
  chk "downgrade --sql 退出码" "0" "0"
else
  echo "  ✗ downgrade --sql 渲染失败"; tail -6 "$tmp/dn.sql.err"; FAIL=1
fi
chk "upgrade 渲染未被截断（含版本推进）" "$(grep -c "SET version_num='$TO_I'" "$tmp/up.sql")" "1"
chk "downgrade 渲染未被截断（含版本回退）" "$(grep -c "SET version_num='$FROM_I'" "$tmp/dn.sql")" "1"
chk "upgrade 渲染含 admin 补种" "$(grep -c "'admin', 'api', 'live_control'" "$tmp/up.sql")" "1"
chk "upgrade 渲染含 trader 补种" "$(grep -c "'trader', 'api', 'live_control'" "$tmp/up.sql")" "1"
chk "upgrade 渲染含幂等子句" "$(grep -c 'ON CONFLICT DO NOTHING' "$tmp/up.sql")" "2"
rm -rf "$tmp"
}

# ── 用例 K：0125/0126/0127 三连（批 86-B 纸上交易），2026-10-02 ──
# 前置 = 0124 之后（live_control 行已在）。三连内容：
#   0125 live_task+mode（CHECK 锁 live/paper）  0126 补种 paper_trade + nav 行
#   0127 paper_trade_log 表 + trading_account.is_virtual + 虚拟账户 + 其 account_permission
# 本用例的价值（不是「upgrade 没报错」）：
#   ① mode 默认值：存量行**必须**被 DEFAULT 'live' 正确语义化（零回填立法的落点）；
#   ② CHECK 拦截：mode='bogus' 必须**被拒**（非法值静默落库=本仓立法明令禁止），
#     且 mode='paper' 正常放行（正反两面都要证）；
#   ③ 两维行数：api（3 行）与 nav（6 行：paper-trade×4 + live-task×2）逐格断言；
#   ④ 隔离真源：虚拟账户恰好 1 行 + 它的 account_permission 三维放行**非空**（0127 注释里
#     「空数组=静默零权限」陷阱的直接防线）；真实账户行不被误标 is_virtual；
#   ⑤ **强制复跑**（比 case_i 的「alembic 已在 head 再 upgrade」更狠）：stamp 回 0124 再
#     upgrade——三个迁移在**已迁移过的 schema 上原样重执行**（对应「部署中断后重跑」的真实场景），
#     依 IF NOT EXISTS / ON CONFLICT / WHERE NOT EXISTS 三重守卫须零副作用；
#   ⑥ 降级回收：表/列/行四层全回收，且**不误伤**真实账户与其权限行。
FROM_K=0124
TO_K=0127

fixture_paper() {
$PSQL <<SQL
SET search_path = $SCRATCH;
-- permission（prod 形状，0124 已应用的语义——含 live_control 两行）
CREATE TABLE permission (
  id BIGSERIAL PRIMARY KEY,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  dimension TEXT NOT NULL DEFAULT 'api',
  resource TEXT NOT NULL,
  effect TEXT NOT NULL DEFAULT 'allow',
  note TEXT, updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (subject_type, subject_id, dimension, resource, effect));
INSERT INTO permission (subject_type,subject_id,dimension,resource,effect,note) VALUES
  ('role','viewer','api','read','allow','0056 seed'),
  ('role','analyst','api','read','allow','0056 seed'),
  ('role','analyst','api','strategy_control','allow','0056 seed'),
  ('role','analyst','api','data_sync','allow','0056 seed'),
  ('role','trader','api','read','allow','0056 seed'),
  ('role','trader','api','strategy_control','allow','0056 seed'),
  ('role','trader','api','halt','allow','0056 seed'),
  ('role','trader','api','trade','allow','0056 seed'),
  ('role','trader','api','live_trading_control','allow','0056 seed'),
  ('role','admin','api','read','allow','0056 seed'),
  ('role','admin','api','halt','allow','0056 seed'),
  ('role','admin','api','alerts_config','allow','0061 seed'),
  ('role','admin','api','live_control','allow','0124 seed'),
  ('role','trader','api','live_control','allow','0124 seed');
-- trading_account（0116 拆表后的形状，**无 is_virtual**——0127 才加）
CREATE TABLE trading_account (
  id bigserial PRIMARY KEY, name text NOT NULL, provider text NOT NULL, market text NOT NULL,
  exchanges text[], account_key text, credentials_encrypted text, params jsonb,
  capabilities text[] NOT NULL, position integer NOT NULL DEFAULT 0, enabled boolean DEFAULT true,
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now());
CREATE UNIQUE INDEX ix_trading_account_account_key ON trading_account (provider, account_key);
INSERT INTO trading_account (name, provider, market, capabilities, account_key) VALUES
  ('xtp主','xtp','astock','{trading,rt_quote}','8888');
-- account_permission（真实形状：三维 NOT NULL DEFAULT '{}'——空数组=零权限的陷阱载体）
CREATE TABLE account_permission (
  account_id bigint PRIMARY KEY,
  allowed_categories text[] NOT NULL DEFAULT '{}',
  allowed_exchanges text[] NOT NULL DEFAULT '{}',
  allowed_boards text[] NOT NULL DEFAULT '{}',
  is_st_allowed boolean NOT NULL DEFAULT false,
  convertible_allowed boolean NOT NULL DEFAULT false,
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
  CONSTRAINT account_permission_account_id_fkey FOREIGN KEY (account_id)
    REFERENCES trading_account(id) ON DELETE CASCADE);
INSERT INTO account_permission (account_id, allowed_categories, allowed_exchanges, allowed_boards, is_st_allowed, convertible_allowed)
  SELECT id, '{stock,etf}', '{SHSE,SZSE}', '{main,star}', false, false FROM trading_account WHERE provider='xtp';
-- live_task（0116 后形状，**无 mode**——0125 才加）+ 一条存量行（验 DEFAULT 'live' 语义化）
CREATE TABLE live_task (
  id bigserial PRIMARY KEY, name text NOT NULL, strategy_id text, symbol text, params jsonb,
  status text NOT NULL DEFAULT 'pending',
  account_id bigint CONSTRAINT fk_live_task_account REFERENCES trading_account(id) ON DELETE RESTRICT,
  initial_capital numeric, created_at timestamptz DEFAULT now());
INSERT INTO live_task (name, status, account_id, initial_capital)
  VALUES ('存量实盘任务', 'pending', (SELECT id FROM trading_account WHERE provider='xtp'), 1000000);
SQL
}

case_k() {
echo
echo "########## 用例 K：0125/0126/0127 三连（mode 默认/CHECK/两维行数/隔离真源/强制复跑） ##########"
reset_scratch
fixture_paper
stamp "$FROM_K"

# --- K1 upgrade 0124→0127（三连一次跑完） ---
step up "$TO_K" "K1 upgrade（0125+0126+0127）"
chk "存量行 mode 被默认为 live（正确语义，非占位）" \
  "$(q "select mode from live_task where name='存量实盘任务'")" "live"
chk "live_task 的 CHECK 存在" \
  "$(q "select count(*) from pg_constraint where conname='live_task_mode_chk' and connamespace=current_schema()::regnamespace")" "1"
chk "paper_trade_log 表已建" \
  "$(q "select count(*) from information_schema.tables where table_name='paper_trade_log' and table_schema=current_schema()")" "1"
chk "paper_trade_log 复合索引已建" \
  "$(q "select count(*) from pg_indexes where indexname='idx_paper_trade_log_task_ts' and schemaname=current_schema()")" "1"
chk "真实账户未被误标 virtual" \
  "$(q "select count(*) from trading_account where provider='xtp' and is_virtual=false")" "1"
chk "虚拟账户恰好 1 行（provider=paper）" \
  "$(q "select count(*) from trading_account where provider='paper' and is_virtual=true")" "1"
chk "虚拟账户的三维放行非空（反『空数组=静默零权限』陷阱）" \
  "$(q "select count(*) from account_permission ap join trading_account ta on ta.id=ap.account_id where ta.provider='paper' and array_length(ap.allowed_categories,1) >= 3 and array_length(ap.allowed_exchanges,1) >= 3 and array_length(ap.allowed_boards,1) >= 3")" "1"
chk "api 维 paper_trade 恰 3 行（analyst/trader/admin，无 viewer）" \
  "$(q "select count(*) from permission where dimension='api' and resource='paper_trade'")" "3"
chk "viewer 无 paper_trade 行" \
  "$(q "select count(*) from permission where subject_id='viewer' and resource='paper_trade'")" "0"
chk "nav 维共 6 行（paper-trade×4 + live-task hidden×2）" \
  "$(q "select count(*) from permission where dimension='nav' and resource in ('paper-trade','live-task')")" "6"
chk "nav paper-trade readwrite × 三角色" \
  "$(q "select count(*) from permission where dimension='nav' and resource='paper-trade' and effect='readwrite'")" "3"
chk "nav paper-trade hidden × viewer" \
  "$(q "select count(*) from permission where dimension='nav' and resource='paper-trade' and effect='hidden'")" "1"
chk "nav live-task hidden × analyst+viewer" \
  "$(q "select count(*) from permission where dimension='nav' and resource='live-task' and effect='hidden' and subject_id in ('analyst','viewer')")" "2"

# --- K2 CHECK 拦截（反证 + 正证） ---
if x "insert into live_task (name, status, mode) values ('非法mode', 'pending', 'bogus')"; then
  echo "  ✗ CHECK 未拦截 mode='bogus'（非法值落库）"; FAIL=1
else
  echo "  ✓ CHECK 拦截 mode='bogus'"
fi
chk "非法行未落库" "$(q "select count(*) from live_task where name='非法mode'")" "0"
if x "insert into live_task (name, status, mode) values ('纸上任务', 'pending', 'paper')"; then
  echo "  ✓ mode='paper' 正常放行"
else
  echo "  ✗ mode='paper' 被误拒（CHECK 值域写错了）"; FAIL=1
fi

# --- K3 强制复跑（stamp 回 0124 再 upgrade：三迁移在已迁移 schema 上原样重执行） ---
stamp "$FROM_K"
step up "$TO_K" "K3 强制复跑（部署中断重跑场景）"
chk "复跑后 mode 列仍 1 个（IF NOT EXISTS）" \
  "$(q "select count(*) from information_schema.columns where table_name='live_task' and column_name='mode' and table_schema=current_schema()")" "1"
chk "复跑后 CHECK 仍 1 个（DO \$\$ 幂等）" \
  "$(q "select count(*) from pg_constraint where conname='live_task_mode_chk' and connamespace=current_schema()::regnamespace")" "1"
chk "复跑后虚拟账户仍恰 1 行（WHERE NOT EXISTS）" \
  "$(q "select count(*) from trading_account where provider='paper'")" "1"
chk "复跑后 api paper_trade 仍 3 行（ON CONFLICT）" \
  "$(q "select count(*) from permission where dimension='api' and resource='paper_trade'")" "3"
chk "复跑后 nav 两资源仍 6 行" \
  "$(q "select count(*) from permission where dimension='nav' and resource in ('paper-trade','live-task')")" "6"
chk "复跑后总行数不变（permission=23：fixture 14 + api 3 + nav 6）" "$(q "select count(*) from permission")" "23"

# --- K4 downgrade 0127→0124（四层回收 + 不误伤） ---
step down "$FROM_K" "K4 downgrade（回收三连）"
chk "paper_trade_log 已删" \
  "$(q "select count(*) from information_schema.tables where table_name='paper_trade_log' and table_schema=current_schema()")" "0"
chk "复合索引已删" \
  "$(q "select count(*) from pg_indexes where indexname='idx_paper_trade_log_task_ts' and schemaname=current_schema()")" "0"
chk "is_virtual 列已删" \
  "$(q "select count(*) from information_schema.columns where table_name='trading_account' and column_name='is_virtual' and table_schema=current_schema()")" "0"
chk "虚拟账户行已删（真实账户保留）" \
  "$(q "select count(*) from trading_account")" "1"
chk "真实账户的 account_permission 保留（不误伤）" \
  "$(q "select count(*) from account_permission")" "1"
chk "api paper_trade 行已清" \
  "$(q "select count(*) from permission where dimension='api' and resource='paper_trade'")" "0"
chk "nav paper-trade 行已清" \
  "$(q "select count(*) from permission where dimension='nav' and resource='paper-trade'")" "0"
chk "nav live-task hidden 行已清" \
  "$(q "select count(*) from permission where dimension='nav' and resource='live-task'")" "0"
chk "mode 列已删（0125 降级）" \
  "$(q "select count(*) from information_schema.columns where table_name='live_task' and column_name='mode' and table_schema=current_schema()")" "0"
chk "CHECK 已删（0125 降级）" \
  "$(q "select count(*) from pg_constraint where conname='live_task_mode_chk' and connamespace=current_schema()::regnamespace")" "0"
chk "live_task 存量行仍在（降级不丢任务）" \
  "$(q "select count(*) from live_task")" "2"
chk "permission 总行数回到 14" "$(q "select count(*) from permission")" "14"
}

# ── 用例 L：0128 虚拟账户 enabled 翻转（批 86-B 彩排红修复），2026-10-03 ──
# 彩排实录（202610030826-072149c staging）：阶段 8 quant-hbcheck wrapper v2 报
# 「行 id=502 键 quant:hb:md-hub:502 缺失/过期（期望 4 行，1 行不健康）」——wrapper
# 期望集 SQL=SELECT id FROM trading_account WHERE enabled（deploy/wrappers/quant-hbcheck:64，
# 「期望集层面不过滤防漏报」是批 66b 立法），0127 种下的虚拟账户进期望集但 md-hub 不为
# 它写心跳 ⇒ 永久红 ⇒ rescue 自动回滚。修在数据侧（enabled 语义=启用中的真实交易通道）。
# 前置注意：fixture_paper=0124 形态（无 is_virtual），而 **stamp 只改版本号不执行迁移体**
# （本用例首跑实证：直接 stamp 0127 ⇒ 0128 撞「column is_virtual does not exist」）——
# 故 L0 须 stamp 0124 真跑三连到 0127（三连本体已由用例 K 验过，此处只借其产物），
# 并显式断言「病灶态起点」（期望集 2 行、虚拟账户 enabled=true）——证明翻转不是空转。
# 本用例的价值（不是「upgrade 没报错」）：
#   ① 病灶清除：enabled 翻 false 后 wrapper 期望集恰 1 行（xtp）——彩排红的直接防线，
#     且正面点名「期望集里不得混入非 xtp 行」；
#   ② 不误伤：真实账户 enabled 不动、is_virtual 语义不碰；
#   ③ 纸任务链路无损：get_virtual_account_id 读 is_virtual（paper_trade.py:47 无 enabled
#     条件）——虚拟账户仍恰 1 行可被找到；
#   ④ 强制复跑幂等：enabled=false 后重跑 0128（WHERE ... AND enabled 不命中）零行更新
#     （部署中断重跑场景）；
#   ⑤ 降级诚实回翻：down 恢复 enabled=true（0127 时点数据语义），且结构零变化
#     （0128 是纯数据迁移，无 DDL——is_virtual 删列是 0127.down 的职责）。
FROM_L=0127
TO_L=0128

case_l() {
echo
echo "########## 用例 L：0128 虚拟账户 enabled 翻转（hbcheck 期望集防线/复跑幂等/降级回翻） ##########"
reset_scratch
fixture_paper
stamp "$FROM_K"
step up "$TO_K" "L0 真跑三连至 0127（借用例 K 产物，stamp 不执行迁移体）"
chk "病灶态起点：虚拟账户 enabled=true（0127 种下即病灶）" \
  "$(q "select count(*) from trading_account where provider='paper' and is_virtual=true and enabled=true")" "1"
chk "病灶态起点：wrapper 期望集 2 行（xtp+虚拟账户——彩排红现场复刻）" \
  "$(q "select count(*) from trading_account where enabled")" "2"

# --- L1 upgrade 0127→0128（病灶清除） ---
step up "$TO_L" "L1 upgrade（0128 enabled 翻转）"
chk "虚拟账户 enabled 已翻 false（病灶清除）" \
  "$(q "select count(*) from trading_account where provider='paper' and is_virtual=true and enabled=false")" "1"
chk "is_virtual 语义不碰（仍 true）" \
  "$(q "select count(*) from trading_account where provider='paper' and is_virtual=true")" "1"
chk "wrapper 期望集恰 1 行（enabled 驱动=只含 xtp；彩排红的直接防线）" \
  "$(q "select count(*) from trading_account where enabled")" "1"
chk "期望集里无非 xtp 行（正面点名）" \
  "$(q "select count(*) from trading_account where enabled and provider<>'xtp'")" "0"
chk "真实账户 enabled 不误伤" \
  "$(q "select count(*) from trading_account where provider='xtp' and enabled=true")" "1"
chk "纸任务读点无损（is_virtual 驱动，无 enabled 条件）" \
  "$(q "select count(*) from trading_account where is_virtual=true")" "1"

# --- L2 强制复跑（stamp 回 0127 再 upgrade：部署中断重跑场景，UPDATE 零行命中） ---
stamp "$FROM_L"
step up "$TO_L" "L2 强制复跑（enabled 已 false，WHERE 不命中）"
chk "复跑后虚拟账户仍恰 1 行且 enabled=false" \
  "$(q "select count(*) from trading_account where provider='paper' and is_virtual=true and enabled=false")" "1"
chk "复跑后期望集仍 1 行" \
  "$(q "select count(*) from trading_account where enabled")" "1"
chk "复跑后真实账户仍 enabled=true" \
  "$(q "select count(*) from trading_account where provider='xtp' and enabled=true")" "1"

# --- L3 downgrade 0128→0127（诚实回翻 + 结构零变化） ---
step down "$FROM_L" "L3 downgrade（回翻 enabled=true，0127 时点语义）"
chk "虚拟账户 enabled 回翻 true（downgrade 诚实还原 0127 时点数据）" \
  "$(q "select count(*) from trading_account where provider='paper' and is_virtual=true and enabled=true")" "1"
chk "真实账户仍 enabled=true" \
  "$(q "select count(*) from trading_account where provider='xtp' and enabled=true")" "1"
chk "is_virtual 列仍在（0128 无 DDL）" \
  "$(q "select count(*) from information_schema.columns where table_name='trading_account' and column_name='is_virtual' and table_schema=current_schema()")" "1"
chk "paper_trade_log 表仍在（结构零变化）" \
  "$(q "select count(*) from information_schema.tables where table_name='paper_trade_log' and table_schema=current_schema()")" "1"
chk "live_task 行数不变（0128 不碰任务）" \
  "$(q "select count(*) from live_task")" "1"
}

# ── 用例 M：0132 加密永续日线配置两行（纯 expand DML），2026-10-06 批 101 ──
# 前置 = 0131 之后（sync_config 已有 supports_backfill/start_floor 两列）。0132 **零 DDL**
# （只 INSERT sync_config + sync_kind_config 各一行）⇒ 无 allow_contract。
# 本用例的价值（不是「upgrade 没报错」）：
#   ① 两行**值级**断言：provider/trade_day_filter/supports_backfill/start_floor/data_type/
#      sync_mode/schedule + kind/sub_kind/pg_table/rebuild——crypto=**连续轴**
#      （trade_day_filter='none'，无交易日历）与 T+1 cron（'30 8 * * *' 北京＝00:30 UTC，
#      在 UTC 前一日文件落盘之后）都是设计要点，写错即**静默错调度**；
#   ② **不误伤**：邻居行（astock_basic 的 start_floor / astock_daily 的 supports_backfill）
#      零改动——防「顺手 UPDATE 整表」式写法；
#   ③ **幂等复跑**（部署中断重跑场景）：stamp 回 0131 再 upgrade，仍恰两行、无重复；
#   ④ **不覆盖运维编辑**：预置运维改过的行 ⇒ ON CONFLICT DO NOTHING 保其值；
#   ⑤ 降级对称回收：两行都删，邻居行不受扰。
FROM_M=0131
TO_M=0132

fixture_crypto() {
$PSQL <<SQL
SET search_path = $SCRATCH;
CREATE TABLE sync_config (
  id text PRIMARY KEY, name text NOT NULL, tushare_api text, pg_table text,
  data_type text NOT NULL, sync_mode text NOT NULL, schedule text,
  enabled boolean NOT NULL DEFAULT true,
  last_sync_date text, last_sync_ts timestamptz, last_sync_count integer DEFAULT 0,
  last_status text, description text, created_at timestamptz DEFAULT now(),
  trade_day_filter text DEFAULT 'none', provider text DEFAULT 'tushare',
  supports_backfill boolean NOT NULL DEFAULT false, start_floor date);
CREATE TABLE sync_kind_config (
  sync_id text PRIMARY KEY, kind text NOT NULL, sub_kind text,
  pg_table text NOT NULL, pk_cols text, float_cols text, text_cols text, rebuild text NOT NULL);
-- 邻居行：一条参考数据（有 start_floor）+ 一条 bar 族（supports_backfill=true）
INSERT INTO sync_config (id,name,tushare_api,pg_table,data_type,sync_mode,schedule,trade_day_filter,provider,supports_backfill,start_floor) VALUES
  ('astock_basic','A股基本面','stock_basic','astock_basic','basic','incremental','0 8 * * *','none','tushare',true,'1990-12-19'),
  ('astock_daily','A股日线','daily','bar_1D','quote','incremental','20 17 * * *','none','tushare',true,NULL);
INSERT INTO sync_kind_config (sync_id,kind,sub_kind,pg_table,pk_cols,float_cols,text_cols,rebuild) VALUES
  ('astock_daily','bar_daily','stock','bar_1d','{symbol,ts}','{open,high,low,close,volume,amount}','{}','incremental');
SQL
}

case_m() {
echo
echo "########## 用例 M：0132 加密永续日线配置（值级 + 不误伤 + 幂等 + 降级） ##########"
reset_scratch
fixture_crypto
stamp "$FROM_M"

# --- M1 upgrade ---
step up "$TO_M" "M1 upgrade（插两行配置）"
chk "sync_config 行已插（值级：provider/过滤/可回补/下界/类型/形态/cron）" \
  "$(q "select provider||'/'||trade_day_filter||'/'||supports_backfill::text||'/'||start_floor::text||'/'||data_type||'/'||sync_mode||'/'||schedule from sync_config where id='crypto_perp_daily'")" \
  "binance/none/true/2019-09-08/crypto/incremental/30 8 * * *"
chk "sync_kind_config 归置行已插（值级）" \
  "$(q "select kind||'/'||sub_kind||'/'||pg_table||'/'||rebuild from sync_kind_config where sync_id='crypto_perp_daily'")" \
  "bar_daily/perp/bar_1d/incremental"
chk "邻居行数不变（sync_config 3 行）" "$(q "select count(*) from sync_config")" "3"
chk "astock_basic 的 start_floor 未被动" \
  "$(q "select start_floor::text from sync_config where id='astock_basic'")" "1990-12-19"
chk "astock_daily 的 supports_backfill 未被动" \
  "$(q "select supports_backfill::text from sync_config where id='astock_daily'")" "true"
chk "sync_kind_config 邻居行数不变（2 行）" "$(q "select count(*) from sync_kind_config")" "2"

# --- M2 幂等复跑（stamp 回 0131 再 upgrade：部署中断重跑场景） ---
stamp "$FROM_M"
step up "$TO_M" "M2 强制复跑（部署中断重跑场景）"
chk "复跑后 crypto 配置行仍恰 1 行" \
  "$(q "select count(*) from sync_config where id='crypto_perp_daily'")" "1"
chk "复跑后 kind 归置行仍恰 1 行" \
  "$(q "select count(*) from sync_kind_config where sync_id='crypto_perp_daily'")" "1"

# --- M3 幂等复跑 + 不覆盖运维编辑（真上产：upgrade 与界面改 schedule 并发） ---
x "update sync_config set schedule='0 9 * * *', enabled=false where id='crypto_perp_daily'"
stamp "$FROM_M"
step up "$TO_M" "M3 幂等复跑（运维已改该行）"
chk "运维改过的 schedule 未被覆盖（ON CONFLICT DO NOTHING）" \
  "$(q "select schedule from sync_config where id='crypto_perp_daily'")" "0 9 * * *"
chk "运维置的 enabled=false 未被翻回" \
  "$(q "select enabled::text from sync_config where id='crypto_perp_daily'")" "false"

# --- M4 downgrade ---
step down "$FROM_M" "M4 downgrade（回收两行）"
chk "crypto 配置行已回收" "$(q "select count(*) from sync_config where id='crypto_perp_daily'")" "0"
chk "kind 归置行已回收" "$(q "select count(*) from sync_kind_config where sync_id='crypto_perp_daily'")" "0"
chk "邻居行未被误删（sync_config）" "$(q "select count(*) from sync_config")" "2"
chk "邻居行未被误删（sync_kind_config）" "$(q "select count(*) from sync_kind_config")" "1"
}

# ── 用例 N：0132 离线渲染完整性（不连库） ──
case_n() {
echo
echo "########## 用例 N：0132 离线渲染完整性（不连库） ##########"
local tmp; tmp="$(mktemp -d)"
if offline_render up "$FROM_M:$TO_M" "$tmp/up.sql"; then
  chk "upgrade --sql 退出码" "0" "0"
else
  echo "  ✗ upgrade --sql 渲染失败"; tail -6 "$tmp/up.sql.err"; FAIL=1
fi
if offline_render down "$TO_M:$FROM_M" "$tmp/dn.sql"; then
  chk "downgrade --sql 退出码" "0" "0"
else
  echo "  ✗ downgrade --sql 渲染失败"; tail -6 "$tmp/dn.sql.err"; FAIL=1
fi
chk "upgrade 渲染未被截断（含版本推进）" "$(grep -c "SET version_num='$TO_M'" "$tmp/up.sql")" "1"
chk "downgrade 渲染未被截断（含版本回退）" "$(grep -c "SET version_num='$FROM_M'" "$tmp/dn.sql")" "1"
chk "upgrade 渲染含两条幂等子句" "$(grep -c 'ON CONFLICT' "$tmp/up.sql")" "2"
chk "upgrade 渲染含 crypto_perp_daily（两行配置各一）" "$(grep -c 'crypto_perp_daily' "$tmp/up.sql")" "2"
rm -rf "$tmp"
}

# ═══════════════════════════ 主流程 ═══════════════════════════
echo "########## 迁移往返回归（scratch=$SCRATCH  revisions=$FROM→$TO + $FROM_D→$TO_D + $FROM_E→$TO_E + $FROM_I→$TO_I + $FROM_K→$TO_K + $FROM_L→$TO_L + $FROM_M→$TO_M） ##########"
precheck
case_a
case_b
case_c
case_d
case_e
case_f
case_g
case_h
case_i
case_j
case_k
case_l
case_m
case_n
finish
