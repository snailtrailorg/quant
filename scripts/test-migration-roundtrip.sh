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

# ═══════════════════════════ 主流程 ═══════════════════════════
echo "########## 迁移往返回归（scratch=$SCRATCH  revisions=$FROM→$TO + $FROM_D→$TO_D + $FROM_E→$TO_E + $FROM_I→$TO_I） ##########"
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
finish
