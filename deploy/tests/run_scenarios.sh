#!/usr/bin/env bash
# run_scenarios.sh —— 批3 3a 六场景注入驱动（设计稿 §6 覆盖声明）
#
#   1 坏 requirements   本地 pip 真验（dry-run 拦截）                  期望非零
#   2 坏迁移           本地 PG 道具（DDL 门拦截，alembic 不触库）      期望非零
#   3 crash-loop       quant-sbx-* 假单元真 systemctl（dwell 判死）   期望非零+回滚恢复
#   4 postverify 失败回滚   【SKIP——诚实改判 3b 服务器盘外彩排】       声明跳过
#   5 中断残留         孤儿 staged 顺带 GC（+GC 逻辑）                 期望零
#   6 单元变更         quant-install-units 通道+受影响波 stabilize     期望零+新单元落位
#
#   附加行: A0 交易窗口闸 / W install-units 负例 / D quant-dbro 负例（v3.3）/
#           P quant-pinned 负例+假 proc 树（v3.3）——均为 wrapper 级直接断言
#
# 每场景断言 ansible-playbook 退出码（非零场景必须非零、成功场景必须零）；
# 末尾汇总表，任一不符则整体非零（CI 门不吃假绿——设计稿退出码语义）。
#
# 用法: bash deploy/tests/run_scenarios.sh
set -uo pipefail

# 双盲审修补: 互斥锁——沙箱树/沙箱单元是共享可变状态，防两实例并发互踩（含沙箱 systemctl --user 单元）
exec 9>"$(dirname "$0")/.run_scenarios.lock"
flock -n 9 || { echo "✗ 已有 run_scenarios 实例在跑（flock 拒并发）" >&2; exit 9; }

HERE=$(cd "$(dirname "$0")" && pwd)
DEPLOY=$(cd "$HERE/.." && pwd)
ROOT=$HERE/sandbox_root/quant
STAGE=$HERE/sandbox_root/stage
PLAY=$DEPLOY/.venv/bin/ansible-playbook
LOGDIR=$HERE/logs
USER_UNITS=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

# 确定性 release_id（正则 ^[0-9]{12}-[a-f0-9]{7,}$）
R_BASE=202608260100-0000aaa
R_NEW=202608260200-0000bbb
R_ORPHAN=202608260050-0000ccc
R_AUX=202608260190-0000fff

mkdir -p "$LOGDIR"
declare -a ROWS=()
FAILED=0

# ---------- 工具 ----------

# 定向复验（2026-10-08 增）：ONLY 非空 ⇒ 只跑 ID 命中的场景（逗号分隔，逐条精确匹配）。
# ⚠ 子集运行**不是**整套验收证据——横幅与汇总均显著标注。
# ⚠ 跨场景依赖：**仅** S6b 依赖 S6 的状态（S6b 断言的是 S6 刚落位的单元指纹）⇒ 单跑须
#   `ONLY=S6,S6b` 成组指定。（更正 2026-10-08：原注释称「7f 依赖 7e 的 seed」**不实**——
#   S7f 自调 `make_sandbox_root.sh` 造全新根，**自包含**，与 S7e 无状态耦合。）
ONLY="${ONLY:-}"
want() {  # want <ID>
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in *",$1,"*) return 0 ;; *) return 1 ;; esac
}

# 整套场景行数（**加场景必须同步 +1**；忘改则汇总的行数守恒守卫会红——fail-closed 方向正确）
FULL_ROWS=27          # A0/A0b/W/W3/D/P(6) + S1/S2/S3/S4/S5/S6(6) + S6b(1) + S7a..S7n(14)
only_count() {  # ONLY 的元素个数（供子集模式的行数守恒）
  local n=0 id
  local IFS=','
  for id in $ONLY; do
    [ -n "$id" ] && n=$((n + 1))
  done
  echo "$n"
}
run_release() {  # run_release <release_id> [额外 -e 参数...]
  local id=$1; shift
  (cd "$DEPLOY" && "$PLAY" playbooks/release.yml -i inventory/sandbox.yml \
     -e "deploy_release_id=$id" "$@")
}

link_id() { readlink -f "$ROOT/server" 2>/dev/null | xargs -r basename; }

check() {  # check <描述> <命令...>；命令退出码判定
  local desc=$1; shift
  if "$@" >/dev/null 2>&1; then
    echo "    ✓ $desc"
  else
    echo "    ✗ $desc  （断言失败）"
    FAILED=$((FAILED + 1))
  fi
}

scenario_row() {  # scenario_row <编号> <名称> <期望> <实际rc> <判定文本>
  ROWS+=("$(printf '%-4s %-18s %-10s %-8s %s' "$1" "$2" "$3" "$4" "$5")")
}

require_stage4() {  # require_stage4 <场景ID> <日志文件>
  # 类级守卫（2026-10-08 增）：反向场景只断言「退出码非零」**不够**——发布可能失败在
  # **别的层**（典型：阶段 3 的 ast.parse 全量先吃掉道具），此时非零 rc 是「假绿」
  # （绿灯给了错的理由）。判据须**把失败钉在阶段 4 的 DDL 门**上。
  # 缘起：S7g（移走 venv 解释器 ⇒ 阶段 3 先炸）、S7i（非法 UTF-8 ⇒ 阶段 3 先炸）两例实测。
  if grep -q "失败任务: 阶段 4" "$2"; then
    echo "    ✓ [$1] 失败确在阶段 4（门任务在案）"
  else
    echo "    ✗ [$1] 未走到阶段 4 —— 断言无效（假绿：非零 rc 来自其它层）"
    FAILED=$((FAILED + 1))
  fi
}

seed_baseline() {  # 全新沙箱 + 基线 release（R_BASE 成功发布，单元在跑）
  bash "$HERE/make_sandbox_root.sh" >>"$LOGDIR/seed.log" 2>&1 || { echo "✗ 沙箱生成失败（见 $LOGDIR/seed.log）"; exit 1; }
  run_release "$R_BASE" >"$LOGDIR/seed-release.log" 2>&1
  local rc=$?
  echo "== 基线发布 $R_BASE rc=$rc =="
  [ $rc -eq 0 ] || { tail -30 "$LOGDIR/seed-release.log"; echo "✗ 基线发布失败，场景中止"; exit 1; }
  check "基线: server 链接 → $R_BASE" test "$(link_id)" = "$R_BASE"
  check "基线: 三单元 is-active" bash -c "systemctl --user is-active quant-sbx-web.service quant-sbx-celery.service quant-sbx-hub.service | grep -vc '^active' | grep -qx 0"
  check "基线: healthz 报告 $R_BASE" bash -c "curl -fsS http://127.0.0.1:18923/healthz | grep -q '\"release\": *\"$R_BASE\"'"
}

echo "=============================================================="
echo " 批3 3a 六场景注入（沙箱零生产触碰）"

if [ -n "$ONLY" ]; then
  echo "⚠⚠ 子集运行（ONLY=$ONLY）——定向复验，**不得**作为整套验收证据 ⚠⚠"
fi
echo "=============================================================="

# ---------- 附加断言: 交易窗口闸（确定性注入 deploy_fake_now=uHHMM） ----------
# 批 106：门现在还要查交易日历 ⇒ 必须同时注入 deploy_fake_date（否则用真实日期——
# 假日跑测试会假绿）。A0 用「周一且开市」的 2026-09-28 保「盘中仍拒绝」语义。
if want A0; then
echo "[A0] 交易窗口闸（周一 09:30 + 交易日 2026-09-28 → enforce=true 须拒绝）"
bash "$HERE/make_sandbox_root.sh" >>"$LOGDIR/a0.log" 2>&1
run_release "$R_AUX" -e "deploy_fake_now=10930" -e "deploy_fake_date=2026-09-28" \
  -e "deploy_trading_window_enforce=true" \
  >"$LOGDIR/a0-release.log" 2>&1
A0_RC=$?
if [ $A0_RC -ne 0 ] && grep -q "交易窗口" "$LOGDIR/a0-release.log"; then
  scenario_row A0 "交易窗口闸" "非零" "$A0_RC" "PASS（盘中拒绝）"
else
  scenario_row A0 "交易窗口闸" "非零" "$A0_RC" "FAIL（须盘中拒绝）"
  FAILED=$((FAILED + 1))
fi
fi

# ---------- 附加断言: 假日放行（批 106 新语义——同一时刻，非交易日不得拦） ----------
# 2026-10-01 是周四（钟点在窗内）但 is_open=0 ⇒ 门须放行（走完 preflight 之外的阶段）。
if want A0b; then
echo "[A0b] 假日放行（周四 09:30 + 假日 2026-10-01 → 不得因交易窗被拒）"
bash "$HERE/make_sandbox_root.sh" >>"$LOGDIR/a0b.log" 2>&1
run_release "$R_AUX" -e "deploy_fake_now=40930" -e "deploy_fake_date=2026-10-01" \
  -e "deploy_trading_window_enforce=true" \
  >"$LOGDIR/a0b-release.log" 2>&1
A0B_RC=$?
if grep -q "盘中（TZ=Asia/Shanghai）拒绝发布" "$LOGDIR/a0b-release.log"; then
  scenario_row A0b "假日放行" "任意" "$A0B_RC" "FAIL（假日误拦）"
  FAILED=$((FAILED + 1))
else
  scenario_row A0b "假日放行" "任意" "$A0B_RC" "PASS（假日未被交易窗拦）"
fi
fi

# ---------- 附加断言: wrapper 负例（双盲审修补①②: 拒符号链接/拒缺 User=quant） ----------
if want W; then
echo "[W] wrapper 负例（quant-install-units 安全校验）"
R_W=202608260080-0000eee
WSRC=$ROOT/releases/$R_W/scripts/systemd
mkdir -p "$WSRC"
printf '[Unit]\nDescription=W ok\n\n[Service]\nExecStart=/bin/true\nUser=quant\n' > "$WSRC/quant-w-ok.service"
ln -s /etc/passwd "$WSRC/quant-w-link.service"          # 负例1 道具: 符号链接单元（glob 序在 ok 前）
QUANT_DEPLOY_ROOT="$ROOT" QUANT_SVC_OPTS=--user QUANT_UNIT_DEST=/tmp/quant-w-neg \
  bash "$DEPLOY/wrappers/quant-install-units" "$R_W" >/dev/null 2>&1
W1=$?
rm -f "$WSRC/quant-w-link.service"
printf '[Unit]\nDescription=W nouser\n\n[Service]\nExecStart=/bin/true\n' > "$WSRC/quant-w-nouser.service"
QUANT_DEPLOY_ROOT="$ROOT" QUANT_SVC_OPTS= QUANT_UNIT_DEST=/tmp/quant-w-neg \
  bash "$DEPLOY/wrappers/quant-install-units" "$R_W" >/dev/null 2>&1
W2=$?
rm -rf "$ROOT/releases/$R_W"                             # 道具清理（校验在拷贝前，本就零副作用）
if [ $W1 -eq 2 ] && [ $W2 -eq 2 ]; then
  scenario_row W "wrapper负例" "exit 2" "$W1/$W2" "PASS（拒链接+拒缺User）"
else
  scenario_row W "wrapper负例" "exit 2" "$W1/$W2" "FAIL"
  FAILED=$((FAILED + 1))
fi
fi

# ---------- 附加断言: quant-flip-web 负例（web 工件化批 2026-08-30——严参/无 web 工件/实目录拒） ----------
if want W3; then
echo "[W3] quant-flip-web 负例"
FW=$DEPLOY/wrappers/quant-flip-web
R_FW=202608260080-0000fff
mkdir -p "$ROOT/releases/$R_FW"                              # 有 release 根、无 web/ 子目录（历史版形态）
QUANT_DEPLOY_ROOT="$ROOT" bash "$FW" >/dev/null 2>&1; F1=$?  # 负例1: 缺参
QUANT_DEPLOY_ROOT="$ROOT" bash "$FW" bad-id >/dev/null 2>&1; F2=$?   # 负例2: 非法 id
QUANT_DEPLOY_ROOT="$ROOT" bash "$FW" "$R_FW" >/dev/null 2>&1; F3=$?  # 负例3: 目标无 web/（A-P1-1 核心）
mkdir -p "$ROOT/web"                                          # 负例4 道具: web 实目录（未迁移形态）
QUANT_DEPLOY_ROOT="$ROOT" bash "$FW" "$R_FW" >/dev/null 2>&1; F4=$?
rm -rf "$ROOT/web" "$ROOT/releases/$R_FW"                     # 道具清理（校验全在切换前，零副作用）
if [ $F1 -eq 2 ] && [ $F2 -eq 2 ] && [ $F3 -eq 1 ] && [ $F4 -eq 1 ]; then
  scenario_row W3 "flip-web负例" "2/2/1/1" "$F1/$F2/$F3/$F4" "PASS（严参+无工件+实目录拒）"
else
  scenario_row W3 "flip-web负例" "2/2/1/1" "$F1/$F2/$F3/$F4" "FAIL"
  FAILED=$((FAILED + 1))
fi
fi

# ---------- 附加断言: quant-dbro 负例（v3.3——越权 which/多余参数/沙箱 SQL 错误非零） ----------
# 沙箱模式 QUANT_DBRO_SQL_DIR 道具: <which>.out=stdout，<which>.err=模拟 DB 错误；
# 每个场景的 preflight 已顺带回归 wrapper 正通道（sandbox group_vars source=db 走空清单道具）
if want D; then
echo "[D] quant-dbro 负例（严参校验 + DB 错误非零退出）"
DBRO=$DEPLOY/wrappers/quant-dbro
DT=$(mktemp -d)
"$DBRO" >/dev/null 2>&1; D1=$?                                # 负例1: 缺参
"$DBRO" drop >/dev/null 2>&1; D2=$?                           # 负例2: 越权 which
"$DBRO" live extra >/dev/null 2>&1; D3=$?                     # 负例3: 多余参数
printf 'ERROR: relation "live_task" does not exist\n' > "$DT/live.err"
QUANT_DBRO_SQL_DIR=$DT "$DBRO" live >/dev/null 2>&1; D4=$?    # 负例4: 沙箱 SQL 错误→非零
rm -f "$DT/live.err"
printf 'quant-live-task@8.service\n' > "$DT/live.out"
QUANT_DBRO_SQL_DIR=$DT "$DBRO" live >"$DT/got" 2>/dev/null; D5=$?
grep -qx 'quant-live-task@8.service' "$DT/got" || D5=99       # 正例回读: 单元名每行一个
rm -rf "$DT"
if [ $D1 -eq 2 ] && [ $D2 -eq 2 ] && [ $D3 -eq 2 ] && [ $D4 -ne 0 ] && [ $D4 -ne 2 ] && [ $D5 -eq 0 ]; then
  scenario_row D "dbro负例" "2/2/2/非零" "$D1/$D2/$D3/$D4" "PASS（严参+错误非零+正例回读）"
else
  scenario_row D "dbro负例" "2/2/2/非零" "$D1/$D2/$D3/$D4" "FAIL（D5=$D5）"
  FAILED=$((FAILED + 1))
fi
fi

# ---------- 附加断言: quant-pinned 负例 + 假 proc 树确定性（v3.3） ----------
# QUANT_PINNED_CWD_DIR 道具: <dir>/<pid>/cwd 形态假树——去重(101/102 同版)/跳过(103 dangling)/
# 过滤(104 非 releases 形态)；严参（多余参数/QUANT_SVC_OPTS 白名单外均 exit 2）
if want P; then
echo "[P] quant-pinned 负例与假 proc 树探测"
PIN=$DEPLOY/wrappers/quant-pinned
PT=$(mktemp -d)
RA=$PT/releases/202608260100-0000aaa
mkdir -p "$PT"/101 "$PT"/102 "$PT"/103 "$PT"/104 "$RA" "$PT/shared"
ln -s "$RA" "$PT/101/cwd"                    # 钉 release（去重样本一）
ln -s "$RA" "$PT/102/cwd"                    # 钉同一 release（去重样本二）
ln -s /nonexistent-sbx-probe "$PT/103/cwd"   # dangling 链接 → 无权读/消失类跳过
ln -s "$PT/shared" "$PT/104/cwd"             # 非 */releases/* 形态 → 过滤
RB=$PT/releases/202608260200-0000bbb
mkdir -p "$RB" "$PT"/105
ln -s "$RB" "$PT/105/cwd"                     # 双版本被钉（B7：postverify 终判正例的输入形态）
QUANT_PINNED_CWD_DIR=$PT "$PIN" >"$PT/got" 2>/dev/null; P1=$?
"$PIN" unexpected >/dev/null 2>&1; P2=$?                            # 负例: 多余参数
QUANT_SVC_OPTS=--root "$PIN" >/dev/null 2>&1; P3=$?                 # 负例: QUANT_SVC_OPTS 白名单外
P_OK=0
[ "$(cat "$PT/got" | tr '\n' ',')" = "202608260100-0000aaa,202608260200-0000bbb," ] || P_OK=1  # 去重+过滤+双版本两行
rm -rf "$PT"
if [ $P1 -eq 0 ] && [ $P2 -eq 2 ] && [ $P3 -eq 2 ] && [ $P_OK -eq 0 ]; then
  scenario_row P "pinned探测" "0/2/2" "$P1/$P2/$P3" "PASS（去重+跳过+过滤+严参）"
else
  scenario_row P "pinned探测" "0/2/2" "$P1/$P2/$P3" "FAIL（确定性=$P_OK）"
  FAILED=$((FAILED + 1))
fi
fi

# ---------- 场景 1: 坏 requirements（本地 pip 真验） ----------
if want S1; then
echo "[S1] 坏 requirements（pip dry-run 拦截）"
seed_baseline
# 批 106：3980c0c 起 quant-pip-wrapper 读的是 **requirements.lock**（--require-hashes），
# 故注入点必须是 lock 而非 .txt——否则 pip 根本看不见，S1 永远拦不住＝假绿。
echo "quant-sbx-nonexistent-broken-package==999.99.99" >>"$STAGE/requirements.lock"
run_release "$R_NEW" >"$LOGDIR/s1.log" 2>&1
S1=$?
echo "  rc=$S1（期望非零）"
[ $S1 -ne 0 ] || FAILED=$((FAILED + 1))
check "S1: staged 已清理（rescue 生效）" test ! -e "$ROOT/releases/$R_NEW"
check "S1: server 链接未动（仍 $R_BASE）" test "$(link_id)" = "$R_BASE"
[ $S1 -ne 0 ] && scenario_row 1 "坏requirements" "非零" "$S1" "PASS" || scenario_row 1 "坏requirements" "非零" "$S1" "FAIL"
fi

# ---------- 场景 2: 坏迁移（本地 PG 道具；DDL 门拦截） ----------
if want S2; then
echo "[S2] 坏迁移（破坏性 DDL 门拦截）"
seed_baseline
cat >"$STAGE/migrations/versions/0099_sbx_bad_drop.py" <<'EOF'
"""坏迁移道具：DROP COLUMN（release.yml 阶段 4 DDL 门应在此拦截，alembic 不触库）"""
revision = "0099"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE demo_t DROP COLUMN payload")


def downgrade() -> None:
    pass
EOF
run_release "$R_NEW" >"$LOGDIR/s2.log" 2>&1
S2=$?
echo "  rc=$S2（期望非零）"
[ $S2 -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "破坏性 DDL 命中" "$LOGDIR/s2.log" && echo "  ✓ DDL 门文本证据在案" || { echo "  ✗ 未捕获 DDL 门拦截证据"; FAILED=$((FAILED + 1)); }
check "S2: staged 已清理" test ! -e "$ROOT/releases/$R_NEW"
check "S2: PG 未被坏迁移触碰（demo_t 列仍在）" bash -c "psql -U quant -h 127.0.0.1 -d quant -tAc \"select column_name from information_schema.columns where table_schema='sbx_deploy' and table_name='demo_t' and column_name='payload'\" | grep -qx payload"
[ $S2 -ne 0 ] && scenario_row 2 "坏迁移(DDL门)" "非零" "$S2" "PASS" || scenario_row 2 "坏迁移(DDL门)" "非零" "$S2" "FAIL"
fi

# ---------- 场景 3: crash-loop（quant-sbx-* 真 systemctl） ----------
if want S3; then
echo "[S3] crash-loop（dwell 判死 → 自动回滚）"
seed_baseline
touch "$STAGE/src/crash.web"
run_release "$R_NEW" >"$LOGDIR/s3.log" 2>&1
S3=$?
echo "  rc=$S3（期望非零）"
[ $S3 -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "自动回滚" "$LOGDIR/s3.log" && echo "  ✓ 回滚路径文本证据在案" || { echo "  ✗ 未捕获回滚证据"; FAILED=$((FAILED + 1)); }
check "S3: 回滚后链接 → $R_BASE" test "$(link_id)" = "$R_BASE"
sleep 2
check "S3: web 单元已恢复 is-active" bash -c "systemctl --user is-active quant-sbx-web.service | grep -qx active"
check "S3: 回滚后 healthz 报告 $R_BASE" bash -c "curl -fsS http://127.0.0.1:18923/healthz | grep -q '\"release\": *\"$R_BASE\"'"
[ $S3 -ne 0 ] && scenario_row 3 "crash-loop回滚" "非零" "$S3" "PASS" || scenario_row 3 "crash-loop回滚" "非零" "$S3" "FAIL"
fi

# ---------- 场景 4: postverify 失败回滚（诚实改判 3b 盘外彩排） ----------
if want S4; then
echo "[S4] postverify 失败回滚 —— 【SKIP】设计稿 §6 改判：服务器盘外彩排（一次性沙箱实例名，不碰真单元）"
scenario_row 4 "postverify失败" "SKIP" "-" "转 3b 盘外彩排"
fi

# ---------- 场景 5: 中断残留（孤儿 staged 顺带 GC） ----------
if want S5; then
echo "[S5] 中断残留 GC"
seed_baseline
mkdir -p "$ROOT/releases/$R_ORPHAN/src"        # 模拟上次中断的半截 staged（无 .deployed）
echo "partial" > "$ROOT/releases/$R_ORPHAN/src/x.py"
# GC keep-N 自证道具（双盲审修补: 数组切片删最旧）: 5 个旧已部署版 + 基线 = 6 个非当前候选，
# keep=5（当前占 1 名额）→ 恰删字典序最旧两（dd1/dd2），留 dd3-dd5+基线+当前
for i in 1 2 3 4 5; do
  mkdir -p "$ROOT/releases/20260826000$i-0000dd$i"
  touch "$ROOT/releases/20260826000$i-0000dd$i/.deployed"
done
# 批 8 快车道根因（2026-09-02 六场景门实证）: R_NEW 与基线同内容→零重启→钉=基线多占一 keep 槽→
# 只删 dd1 不删 dd2——S5 的 keep-N 断言假设"钉=当前"。S5 测经典 GC 语义故显式 force_full；
# 快车道下的 GC 形态（钉旧版被保留）由 S6b 新增断言覆盖。
run_release "$R_NEW" -e force_full=true >"$LOGDIR/s5.log" 2>&1
S5=$?
echo "  rc=$S5（期望零）"
[ $S5 -eq 0 ] || { FAILED=$((FAILED + 1)); tail -30 "$LOGDIR/s5.log"; }
check "S5: 孤儿 staged 已被 GC" test ! -e "$ROOT/releases/$R_ORPHAN"
check "S5: 基线版保留（N=5 内）" test -d "$ROOT/releases/$R_BASE"
check "S5: server 链接 → $R_NEW" test "$(link_id)" = "$R_NEW"
check "S5: alembic 版本表到 head（真 PG 链路）" bash -c "psql -U quant -h 127.0.0.1 -d quant -tAc 'select version_num from sbx_deploy.alembic_version' | grep -qx 0001"
check "S5: keep-N 删最旧两（dd1/dd2）" bash -c "test ! -e '$ROOT/releases/202608260001-0000dd1' && test ! -e '$ROOT/releases/202608260002-0000dd2'"
check "S5: keep-N 留 dd3-dd5+基线+当前（共 5）" bash -c "test -d '$ROOT/releases/202608260003-0000dd3' && test -d '$ROOT/releases/202608260004-0000dd4' && test -d '$ROOT/releases/202608260005-0000dd5' && test -d '$ROOT/releases/$R_BASE' && test -d '$ROOT/releases/$R_NEW'"
[ $S5 -eq 0 ] && scenario_row 5 "中断残留GC" "零" "$S5" "PASS" || scenario_row 5 "中断残留GC" "零" "$S5" "FAIL"
fi

# ---------- 场景 6: 单元变更（install-units 通道） ----------
if want S6; then
echo "[S6] 单元变更通道"
seed_baseline
sed -i 's/桩 v1/桩 v2/' "$STAGE/scripts/systemd/quant-sbx-web.service"
run_release "$R_NEW" >"$LOGDIR/s6.log" 2>&1
S6=$?
echo "  rc=$S6（期望零）"
[ $S6 -eq 0 ] || { FAILED=$((FAILED + 1)); tail -30 "$LOGDIR/s6.log"; }
check "S6: 新单元已落位（含 v2 标记）" grep -q "桩 v2" "$USER_UNITS/quant-sbx-web.service"
check "S6: daemon-reload 后单元在跑（is-active）" bash -c "systemctl --user is-active quant-sbx-web.service | grep -qx active"
check "S6: server 链接 → $R_NEW" test "$(link_id)" = "$R_NEW"
[ $S6 -eq 0 ] && scenario_row 6 "单元变更通道" "零" "$S6" "PASS" || scenario_row 6 "单元变更通道" "零" "$S6" "FAIL"
fi

# ---------- S6b: 同单元内容不同 release_id → units_changed=false（指纹剥路径自证，双盲审修补②） ----------
if want S6b; then
echo "[S6b] 同内容再发布（units_changed=false，安装通道 skipped）"
R_SAME=202608260300-0000ddd
run_release "$R_SAME" >"$LOGDIR/s6b.log" 2>&1
S6B=$?
echo "  rc=$S6B（期望零）"
[ $S6B -eq 0 ] || { FAILED=$((FAILED + 1)); tail -30 "$LOGDIR/s6b.log"; }
check "S6b: 单元安装通道 skipped（指纹对内容敏感、对 release_id 不敏感）" \
  awk '/^TASK \[/ { f = (/单元安装通道/) ? 1 : 0; next } f && /skipping/ { found = 1 } /^PLAY RECAP/ { exit } END { exit !found }' "$LOGDIR/s6b.log"
check "S6b: server 链接 → $R_SAME" test "$(link_id)" = "$R_SAME"
# 批 8 快车道 GC 形态断言（2026-09-02）：同内容再发布零重启→无已部署版被删（被钉基线+当前全保留）
check "S6b: 快车道零删除（基线与上一发布版原样保留——被钉 bbb + 当前 ddd 全跳过）" \
  bash -c "test -d '$ROOT/releases/$R_BASE' && test -d '$ROOT/releases/$R_NEW'"
[ $S6B -eq 0 ] && scenario_row 6 "同内容再发布" "零" "$S6B" "PASS（units_changed=false）" || scenario_row 6 "同内容再发布" "零" "$S6B" "FAIL"
fi

# ---------- S7: 破坏性 DDL 两步走门（批 111——判定真源=migration_policy.py，十三例） ----------
# 道具生成器：write_mig <文件名> <首行声明（可空）> <upgrade 体> [目标目录，默认 STAGE]
write_mig() {
  local fname=$1 decl=$2 body=$3 dest=${4:-$STAGE/migrations/versions}
  local rid=${fname%%_*}
  {
    [ -n "$decl" ] && echo "$decl"
    echo '"""sbx S7 道具"""'
    echo 'revision = "'"$rid"'"'
    echo 'down_revision = "0001"'
    echo 'branch_labels = None'
    echo 'depends_on = None'
    echo
    echo 'def upgrade() -> None:'
    echo '    from alembic import op'
    echo
    echo "    $body"
    echo
    echo 'def downgrade() -> None:'
    echo '    pass'
  } >"$dest/$fname"
}

if want S7a; then
echo "[S7a] 两步走正例：expand 上产 → contract 上产（跨发布）"
seed_baseline
R_EXP=202608260210-0000eee
R_CON=202608260220-0000abc
write_mig "0098_sbx_expand_add.py" '# EXPAND-CONTRACT: phase=expand pair=0099' \
  'op.execute("CREATE TABLE sbx_new_t (id serial PRIMARY KEY)")'
run_release "$R_EXP" >"$LOGDIR/s7a1.log" 2>&1
S7A1=$?
echo "  expand rc=$S7A1（期望零）"
[ $S7A1 -eq 0 ] || { FAILED=$((FAILED + 1)); tail -20 "$LOGDIR/s7a1.log"; }
check "S7a: expand 上产（server → $R_EXP）" test "$(link_id)" = "$R_EXP"
cat >"$STAGE/migrations/versions/0099_sbx_contract_drop.py" <<'EOF'
# EXPAND-CONTRACT: phase=contract pair=0098
"""sbx S7a contract 步：DROP 旧表（收口两步走）"""
revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE demo_t")


def downgrade() -> None:
    pass
EOF
run_release "$R_CON" >"$LOGDIR/s7a2.log" 2>&1
S7A2=$?
echo "  contract rc=$S7A2（期望零——pair=0098 已随 $R_EXP 上产）"
[ $S7A2 -eq 0 ] || { FAILED=$((FAILED + 1)); tail -30 "$LOGDIR/s7a2.log"; }
check "S7a: contract 放行（server → $R_CON）" test "$(link_id)" = "$R_CON"
check "S7a: contract 真跑（alembic_version=0099）" bash -c "psql -U quant -h 127.0.0.1 -d quant -tAc 'select version_num from sbx_deploy.alembic_version' | grep -qx 0099"
[ $S7A1 -eq 0 ] && [ $S7A2 -eq 0 ] && scenario_row 7a "两步走正例" "零/零" "$S7A1/$S7A2" "PASS（跨发布）" || scenario_row 7a "两步走正例" "零/零" "$S7A1/$S7A2" "FAIL"
fi

if want S7b; then
echo "[S7b] 命中但无声明 ⇒ rc=1（与 S2 同构，独立成例）"
seed_baseline
write_mig "0099_sbx_bad_drop.py" "" 'op.execute("DROP TABLE demo_t")'
run_release "$R_NEW" >"$LOGDIR/s7b.log" 2>&1
S7B=$?
echo "  rc=$S7B（期望非零）"
[ $S7B -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "破坏性 DDL 命中" "$LOGDIR/s7b.log" && echo "  ✓ 判据接口文本在案" || { echo "  ✗ 未捕获命中文本"; FAILED=$((FAILED + 1)); }
require_stage4 7b "$LOGDIR/s7b.log"
[ $S7B -ne 0 ] && scenario_row 7b "无声明命中" "非零" "$S7B" "PASS" || scenario_row 7b "无声明命中" "非零" "$S7B" "FAIL"
fi

if want S7c; then
echo "[S7c] contract 但 pair 不在已部署链 ⇒ rc=1（两步走要求跨发布）"
seed_baseline
write_mig "0099_sbx_orphan_c.py" '# EXPAND-CONTRACT: phase=contract pair=0096' \
  'op.execute("DROP TABLE demo_t")'
run_release "$R_NEW" >"$LOGDIR/s7c.log" 2>&1
S7C=$?
echo "  rc=$S7C（期望非零）"
[ $S7C -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "两步走要求" "$LOGDIR/s7c.log" && echo "  ✓ 跨发布语义文本在案" || { echo "  ✗ 未捕获跨发布文本"; FAILED=$((FAILED + 1)); }
require_stage4 7c "$LOGDIR/s7c.log"
[ $S7C -ne 0 ] && scenario_row 7c "pair未上产" "非零" "$S7C" "PASS" || scenario_row 7c "pair未上产" "非零" "$S7C" "FAIL"
fi

if want S7d; then
echo "[S7d] 同名迁移内容变更 ⇒ 纳入受管集（内容指纹差集，非文件名差集）"
seed_baseline
sed -i 's/^def upgrade() -> None:$/def upgrade() -> None:\n    import os; _ = os.environ.get("SBX_TOUCH")\n    op.execute("DROP TABLE demo_t")/' \
  "$STAGE/migrations/versions/0001_sbx_baseline.py"
grep -q "DROP TABLE demo_t" "$STAGE/migrations/versions/0001_sbx_baseline.py" || { echo "  ✗ 注入失败"; FAILED=$((FAILED + 1)); }
run_release "$R_NEW" >"$LOGDIR/s7d.log" 2>&1
S7D=$?
echo "  rc=$S7D（期望非零——0001 内容变更且命中）"
[ $S7D -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "破坏性 DDL 命中: 0001_sbx_baseline.py" "$LOGDIR/s7d.log" && echo "  ✓ 内容变更被受管集捕获" || { echo "  ✗ 同名变更未被捕获（文件名差集复活？）"; FAILED=$((FAILED + 1)); }
require_stage4 7d "$LOGDIR/s7d.log"
[ $S7D -ne 0 ] && scenario_row 7d "同名内容变更" "非零" "$S7D" "PASS" || scenario_row 7d "同名内容变更" "非零" "$S7D" "FAIL"
fi

if want S7e; then
echo "[S7e] 逃生门：allow_contract+contract_reason ⇒ 不阻断 + 告警 + 留痕"
seed_baseline
write_mig "0099_sbx_bad_drop.py" "" 'op.execute("DROP TABLE demo_t")'
run_release "$R_NEW" -e allow_contract=true -e 'contract_reason=S7e 调试期显式豁免（场景注入）' >"$LOGDIR/s7e.log" 2>&1
S7E=$?
echo "  rc=$S7E（期望零——只取消 rc=1 的阻断）"
[ $S7E -eq 0 ] || { FAILED=$((FAILED + 1)); tail -20 "$LOGDIR/s7e.log"; }
grep -q "豁免阻断" "$LOGDIR/s7e.log" && echo "  ✓ 旁路告警在案" || { echo "  ✗ 旁路告警缺失"; FAILED=$((FAILED + 1)); }
check "S7e: 留痕落 var/（不落 release 树）" test -f "$ROOT/var/ddl-gate-bypassed-$R_NEW.txt"
[ $S7E -eq 0 ] && scenario_row 7e "逃生门豁免" "零" "$S7E" "PASS（告警+留痕）" || scenario_row 7e "逃生门豁免" "零" "$S7E" "FAIL"
fi

if want S7f; then
echo "[S7f] 首部署（无上一版）⇒ rc=3 门跳过 + 显著告警，发布不阻断"
bash "$HERE/make_sandbox_root.sh" >>"$LOGDIR/s7f-seed.log" 2>&1
run_release "$R_BASE" >"$LOGDIR/s7f.log" 2>&1
S7F=$?
echo "  rc=$S7F（期望零——首部署窗口门不可判定但放行）"
[ $S7F -eq 0 ] || { FAILED=$((FAILED + 1)); tail -20 "$LOGDIR/s7f.log"; }
grep -q "首部署" "$LOGDIR/s7f.log" && echo "  ✓ 首部署告警在案" || { echo "  ✗ 首部署告警缺失"; FAILED=$((FAILED + 1)); }
[ $S7F -eq 0 ] && scenario_row 7f "首部署rc=3" "零" "$S7F" "PASS（告警不阻断）" || scenario_row 7f "首部署rc=3" "零" "$S7F" "FAIL"
fi

if want S7g; then
echo "[S7g] 门不可用恒红：门脚本缺失（rc=2）即使 allow_contract=true ⇒ fail"
seed_baseline
write_mig "0099_sbx_bad_drop.py" "" 'op.execute("DROP TABLE demo_t")'
# ⚠ 结构钉（2026-10-08 修）：旧写法是 mv 走 shared/venv/bin/python 制造「解释器缺失」——
#   但阶段 3（quant-importsmoke-wrapper）用的**正是同一个解释器** ⇒ 阶段 3 必然先失败
#   ⇒ 永远到不了阶段 4（实测 s7g.log 全文无任何「阶段 4」TASK）⇒ 唯一判据 rc≠0 由「阶段 3
#   失败」即可满足 ⇒ **从上线起就不可能真测到门＝假绿**（且行内 PASS 文本「rc=127 恒红」也是谎）。
#   改移走**门脚本本身**：py_compile 全量 + 六入口 import 冒烟均不碰 migration_policy.py
#   （全仓实测无任何运行时入口 import 它）⇒ 阶段 3 通过；阶段 4 以
#   /usr/bin/python3 "$rel/src/data_platform/migration_policy.py" 执行时文件不存在
#   （解释器已于 2026-10-08 由 shared/venv/bin/python 改为系统 python3——见 release.yml 阶段 4 注）
#   ⇒ Python rc=2 ⇒ 不在放行集 {0, 3, 1+allow_contract} ⇒ 恒红。
#   ⇒ 判据因此**必须**含「失败落在阶段 4」（见下），否则阶段 3 的失败会再次冒充门恒红。
mv "$STAGE/src/data_platform/migration_policy.py" "$STAGE/src/data_platform/migration_policy.py.s7g-bak"
run_release "$R_NEW" -e allow_contract=true -e 'contract_reason=S7g 调试期豁免（门不可用须恒红）' >"$LOGDIR/s7g.log" 2>&1
S7G=$?
mv "$STAGE/src/data_platform/migration_policy.py.s7g-bak" "$STAGE/src/data_platform/migration_policy.py"   # 恢复，不留残沙
echo "  rc=$S7G（期望非零——封闭式补集：2 不在放行集）"
[ $S7G -ne 0 ] || FAILED=$((FAILED + 1))
# 核心断言：失败**必须内生于阶段 4**。缺此条 ⇒ 阶段 3 的失败冒充「门恒红」（旧写法的病根）。
require_stage4 7g "$LOGDIR/s7g.log"
[ $S7G -ne 0 ] && scenario_row 7g "门不可用恒红" "非零" "$S7G" "PASS（rc=2 且失败在阶段 4）" || scenario_row 7g "门不可用恒红" "非零" "$S7G" "FAIL（逃生门越界豁免了门崩溃！）"
fi

if want S7h; then
echo "[S7h] 非冻结 legacy 自声明 ⇒ rc=1（P0-1：门**验证** legacy 标记，不再零验证放行）"
seed_baseline
write_mig "0199_sbx_evil.py" '# EXPAND-CONTRACT: legacy reason="自称历史遗留"' 'op.execute("DROP TABLE demo_t")'
run_release "$R_NEW" >"$LOGDIR/s7h.log" 2>&1
S7H=$?
echo "  rc=$S7H（期望非零——非冻结 legacy 拒）"
[ $S7H -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "非冻结 legacy" "$LOGDIR/s7h.log" && echo "  ✓ 非冻结 legacy 判据在案" || { echo "  ✗ 未捕获非冻结 legacy"; FAILED=$((FAILED + 1)); }
require_stage4 7h "$LOGDIR/s7h.log"
[ $S7H -ne 0 ] && scenario_row 7h "非冻结legacy" "非零" "$S7H" "PASS" || scenario_row 7h "非冻结legacy" "非零" "$S7H" "FAIL（门零验证放行了自声明 legacy！）"
fi

if want S7i; then
echo "[S7i] 门内部错 ⇒ fail-closed 恒红（rc=2）——即使 allow_contract=true（P0-2）"
seed_baseline
# ⚠ 结构钉（2026-10-08 修，原道具＝「迁移文件写入非法 UTF-8」）：
#   阶段 3 的 ast.parse **全量扫 release 树**（rglob *.py）且**只 except SyntaxError** ⇒
#   非法 UTF-8 触发的 UnicodeDecodeError 让**阶段 3 先炸**（实测 s7i.log：失败任务＝阶段 3、
#   实得 rc=1），门根本没跑到 ⇒ 旧断言「rc≠0」是**假绿**，且行内 PASS 文本自称「rc=2」与
#   实得 rc=1 不符。⇒ 任何落在 release .py 树上的坏文件都会被阶段 3 先吃，**不能用作道具**。
#   改注入**合法语法 + 合法编码**的运行时错：覆盖 `_classify_managed`（`check_release` 在
#   其 try 内调用它）——ast.parse 过、六入口 import 不碰 migration_policy ⇒ 阶段 3 过；
#   运行时抛 ⇒ 被 check_release 的 `except Exception` 捕获 ⇒ rc=2 + 打印「DDL 门内部错误」。
python3 - "$STAGE/src/data_platform/migration_policy.py" <<'PY'
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
src = p.read_text(encoding="utf-8")
marker = 'if __name__ == "__main__":'
assert marker in src, "未找到 __main__ 守卫——注入锚点失效"
inject = (
    "# --- S7i 注入：门内部错（合法语法/编码 ⇒ 过阶段 3；check_release 的 except 须恒返 2）---\n"
    "def _classify_managed(*_a, **_k):  # noqa: ANN001, ANN002, ANN003\n"
    '    raise RuntimeError("S7i 注入：门内部错")\n'
    "\n\n"
)
p.write_text(src.replace(marker, inject + marker, 1), encoding="utf-8")
print("  ✓ 注入完成：_classify_managed 覆盖为抛错（语法合法 ⇒ ast.parse 不受影响）")
PY
run_release "$R_NEW" -e allow_contract=true -e 'contract_reason=S7i 门不可用须恒红' >"$LOGDIR/s7i.log" 2>&1
S7I=$?
echo "  rc=$S7I（期望非零）"
[ $S7I -ne 0 ] || FAILED=$((FAILED + 1))
# 强断言：门**自己的** except 分支必须触发（文本由 check_release 的 except 分支产出）。
grep -q "DDL 门内部错误" "$LOGDIR/s7i.log" && echo "    ✓ 门内部错判据文本在案（except 分支确已触发）" \
  || { echo "    ✗ 未捕获『DDL 门内部错误』——门崩溃未被 fail-closed 捕获"; FAILED=$((FAILED + 1)); }
require_stage4 7i "$LOGDIR/s7i.log"
[ $S7I -ne 0 ] && scenario_row 7i "内部错恒红" "非零" "$S7I" "PASS（门内部错 fail-closed）" || scenario_row 7i "内部错恒红" "非零" "$S7I" "FAIL（门崩溃被 allow_contract 豁免！）"
fi

if want S7j; then
echo "[S7j] 冻结集内文件 body 篡改 + 重标 phase=contract ⇒ rc=1（P0-A：冻结集 keying＝文件名，与声明解耦）"
seed_baseline
# 道具：把「已上产的冻结 legacy ＋ 它的 expand 对手」直接写进**已部署版**迁移目录——
# 冻结集文件的定义就是「历史遗留、必已上产」，而沙箱基线是精简面，历史只能这样造。
DEPLOYED="$(readlink -f "$ROOT/server")/migrations/versions"
write_mig "0098_sbx_expand_add.py" '# EXPAND-CONTRACT: phase=expand pair=0099' \
  'op.execute("SELECT 1")' "$DEPLOYED"
write_mig "0100_venue_backfill.py" '# EXPAND-CONTRACT: legacy reason="历史遗留"' \
  'op.execute("SELECT 1")' "$DEPLOYED"
# 本版：同一冻结集文件 body 篡改 + 头重标 contract（旧实现按自声明触发比对 ⇒ rc=0 放行）
write_mig "0100_venue_backfill.py" '# EXPAND-CONTRACT: phase=contract pair=0098' \
  'op.execute("DROP TABLE demo_t")'
run_release "$R_NEW" >"$LOGDIR/s7j.log" 2>&1
S7J=$?
echo "  rc=$S7J（期望非零——冻结集 body 被改须拒，重标 contract 不得旁路）"
[ $S7J -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "发生变化" "$LOGDIR/s7j.log" && echo "  ✓ 冻结集 body 比对判据在案" || { echo "  ✗ 未捕获冻结集 body 比对"; FAILED=$((FAILED + 1)); }
require_stage4 7j "$LOGDIR/s7j.log"
[ $S7J -ne 0 ] && scenario_row 7j "冻结集重标绕过" "非零" "$S7J" "PASS" || scenario_row 7j "冻结集重标绕过" "非零" "$S7J" "FAIL（重标 contract 旁路了冻结集 body 校验！）"
fi

if want S7k; then
echo "[S7k] 小写 SQL（drop table demo_t）⇒ rc=1（P0-B：判定表须大小写不敏感）"
seed_baseline
write_mig "0099_sbx_lower_drop.py" "" 'op.execute("drop table demo_t")'
run_release "$R_NEW" >"$LOGDIR/s7k.log" 2>&1
S7K=$?
echo "  rc=$S7K（期望非零——小写 SQL 与大写同判）"
[ $S7K -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "破坏性 DDL 命中" "$LOGDIR/s7k.log" && echo "  ✓ 小写 SQL 命中判据在案" || { echo "  ✗ 未捕获小写 SQL 命中"; FAILED=$((FAILED + 1)); }
require_stage4 7k "$LOGDIR/s7k.log"
[ $S7K -ne 0 ] && scenario_row 7k "小写SQL" "非零" "$S7K" "PASS" || scenario_row 7k "小写SQL" "非零" "$S7K" "FAIL（小写破坏性 SQL 静默放行！）"
fi

if want S7l; then
echo "[S7l] 排除项不得整句赦免：DROP TABLE 后跟注释里的 drop constraint ⇒ rc=1（P0-C：豁免的是形态非语句）"
seed_baseline
# ⚠ 本例是**回归钉**：re.IGNORECASE 之前小写 `drop constraint` 不匹配排除项 ⇒ 旧实现反而命中；
#   加 re.I 后「语句含关键词即整句跳过」⇒ 由红变绿。门不得比修复前更弱。
write_mig "0099_sbx_comment_wash.py" "" 'op.execute("DROP TABLE demo_t -- drop constraint later")'
run_release "$R_NEW" >"$LOGDIR/s7l.log" 2>&1
S7L=$?
echo "  rc=$S7L（期望非零——注释/并列 action 中的排除项关键词不得洗白同语句的 DROP）"
[ $S7L -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "破坏性 DDL 命中" "$LOGDIR/s7l.log" && echo "  ✓ 排除项减法判据在案" || { echo "  ✗ 未捕获（整句赦免复活？）"; FAILED=$((FAILED + 1)); }
require_stage4 7l "$LOGDIR/s7l.log"
[ $S7L -ne 0 ] && scenario_row 7l "排除项整句赦免" "非零" "$S7L" "PASS" || scenario_row 7l "排除项整句赦免" "非零" "$S7L" "FAIL（排除项洗白了同语句的破坏性 op！）"
fi

if want S7m; then
echo "[S7m] 排除项不得跨分隔符：SET DEFAULT 与破坏性 action 并列 ⇒ rc=1（P0-D：豁免的只是一个 action）"
seed_baseline
# ⚠ 本例是**回归钉**：排除项 #3 曾写 `[^;\n]*`（贪婪跨逗号**与空格**）⇒ 把同语句的
#   `ALTER COLUMN … TYPE`（§1.1 明列 op）一并摘掉 ⇒ 相对最初基线 9fa0e7f 检测面缩小。
write_mig "0099_sbx_greedy_wash.py" "" \
  'op.execute("ALTER TABLE demo_t ALTER COLUMN payload TYPE varchar(8), ALTER COLUMN v SET DEFAULT 1")'
run_release "$R_NEW" >"$LOGDIR/s7m.log" 2>&1
S7M=$?
echo "  rc=$S7M（期望非零——SET DEFAULT 豁免不得吃掉同语句的 TYPE 变更）"
[ $S7M -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "破坏性 DDL 命中" "$LOGDIR/s7m.log" && echo "  ✓ 单 action 收窄判据在案" || { echo "  ✗ 未捕获（贪婪跨分隔符复活？）"; FAILED=$((FAILED + 1)); }
require_stage4 7m "$LOGDIR/s7m.log"
[ $S7M -ne 0 ] && scenario_row 7m "排除项跨分隔符" "非零" "$S7M" "PASS" || scenario_row 7m "排除项跨分隔符" "非零" "$S7M" "FAIL（排除项吃掉了同语句的破坏性 action！）"
fi

if want S7n; then
echo "[S7n] 排除项须保留 ALTER COLUMN 锚点：SET DEFAULT 后的并列 TYPE ⇒ rc=1（P1-G 减法残留洞）"
seed_baseline
# ⚠ 摘除 SET DEFAULT 时若连 `ALTER COLUMN` 锚点一起删 ⇒ 残句失配 ⇒ TYPE 变更静默放行。
write_mig "0099_sbx_anchor_lost.py" "" \
  'op.execute("ALTER TABLE demo_t ALTER COLUMN payload SET DEFAULT 1 , TYPE varchar(8)")'
run_release "$R_NEW" >"$LOGDIR/s7n.log" 2>&1
S7N=$?
echo "  rc=$S7N（期望非零——锚点保留后并列 TYPE 仍须命中）"
[ $S7N -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "破坏性 DDL 命中" "$LOGDIR/s7n.log" && echo "  ✓ 锚点保留判据在案" || { echo "  ✗ 未捕获（锚点被摘除？）"; FAILED=$((FAILED + 1)); }
require_stage4 7n "$LOGDIR/s7n.log"
[ $S7N -ne 0 ] && scenario_row 7n "锚点保留" "非零" "$S7N" "PASS" || scenario_row 7n "锚点保留" "非零" "$S7N" "FAIL（SET DEFAULT 摘除吃掉了并列 TYPE！）"
fi

if [ -n "$ONLY" ]; then
  echo "⚠⚠ 子集运行（ONLY=$ONLY）——定向复验，**不得**作为整套验收证据 ⚠⚠"
fi

# ---------- 汇总 ----------
echo
echo "=============================================================="
echo " 场景汇总（断言 ansible-playbook 退出码 + 场景态断言）"
echo "=============================================================="
printf '%-4s %-18s %-10s %-8s %s\n' "编号" "场景" "期望" "实际" "判定"
for row in "${ROWS[@]}"; do echo "$row"; done
echo "--------------------------------------------------------------"

# 行数守恒守卫（2026-10-08 增）：**绿不能来自「少了行」**。
# 缘起：汇总旧判据只有 `FAILED`——某场景被静默跳过（`fi` 吞掉下一块、`want` 判据写错）时
# 它的行根本不出现，`FAILED` 仍为 0 ⇒ 照旧打印「全绿」。这与 S7g/S7i 的假绿同族
# （判据被绕过），只是绕过的层换成了汇总层。
EXPECT_ROWS=$FULL_ROWS
[ -n "$ONLY" ] && EXPECT_ROWS=$(only_count)
if [ "${#ROWS[@]}" -ne "$EXPECT_ROWS" ]; then
  echo "✗ 场景行数 ${#ROWS[@]} ≠ 期望 $EXPECT_ROWS —— 有场景被静默跳过（须修，不得当绿）"
  FAILED=$((FAILED + 1))
fi

if [ $FAILED -eq 0 ]; then
  if [ -n "$ONLY" ]; then
    echo "结果: 子集全绿（ONLY=$ONLY）——**不是**整套验收证据（整套须不带 ONLY 跑一次）"
  else
    echo "结果: 全绿（S4 为设计稿 §6 声明的诚实跳过）"
  fi
else
  echo "结果: $FAILED 项断言失败（详见 $LOGDIR/）"
fi
exit $FAILED
