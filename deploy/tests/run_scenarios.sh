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
echo "=============================================================="

# ---------- 附加断言: 交易窗口闸（确定性注入 deploy_fake_now=uHHMM） ----------
# 批 106：门现在还要查交易日历 ⇒ 必须同时注入 deploy_fake_date（否则用真实日期——
# 假日跑测试会假绿）。A0 用「周一且开市」的 2026-09-28 保「盘中仍拒绝」语义。
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

# ---------- 附加断言: 假日放行（批 106 新语义——同一时刻，非交易日不得拦） ----------
# 2026-10-01 是周四（钟点在窗内）但 is_open=0 ⇒ 门须放行（走完 preflight 之外的阶段）。
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

# ---------- 附加断言: wrapper 负例（双盲审修补①②: 拒符号链接/拒缺 User=quant） ----------
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

# ---------- 附加断言: quant-flip-web 负例（web 工件化批 2026-08-30——严参/无 web 工件/实目录拒） ----------
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

# ---------- 附加断言: quant-dbro 负例（v3.3——越权 which/多余参数/沙箱 SQL 错误非零） ----------
# 沙箱模式 QUANT_DBRO_SQL_DIR 道具: <which>.out=stdout，<which>.err=模拟 DB 错误；
# 每个场景的 preflight 已顺带回归 wrapper 正通道（sandbox group_vars source=db 走空清单道具）
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

# ---------- 附加断言: quant-pinned 负例 + 假 proc 树确定性（v3.3） ----------
# QUANT_PINNED_CWD_DIR 道具: <dir>/<pid>/cwd 形态假树——去重(101/102 同版)/跳过(103 dangling)/
# 过滤(104 非 releases 形态)；严参（多余参数/QUANT_SVC_OPTS 白名单外均 exit 2）
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

# ---------- 场景 1: 坏 requirements（本地 pip 真验） ----------
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

# ---------- 场景 2: 坏迁移（本地 PG 道具；DDL 门拦截） ----------
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

# ---------- 场景 3: crash-loop（quant-sbx-* 真 systemctl） ----------
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

# ---------- 场景 4: postverify 失败回滚（诚实改判 3b 盘外彩排） ----------
echo "[S4] postverify 失败回滚 —— 【SKIP】设计稿 §6 改判：服务器盘外彩排（一次性沙箱实例名，不碰真单元）"
scenario_row 4 "postverify失败" "SKIP" "-" "转 3b 盘外彩排"

# ---------- 场景 5: 中断残留（孤儿 staged 顺带 GC） ----------
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

# ---------- 场景 6: 单元变更（install-units 通道） ----------
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

# ---------- S6b: 同单元内容不同 release_id → units_changed=false（指纹剥路径自证，双盲审修补②） ----------
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

# ---------- S7: 破坏性 DDL 两步走门（批 111——判定真源=migration_policy.py，七例） ----------
# 道具生成器：write_mig <文件名> <首行声明（可空）> <upgrade 体>
write_mig() {
  local fname=$1 decl=$2 body=$3
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
  } >"$STAGE/migrations/versions/$fname"
}

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

echo "[S7b] 命中但无声明 ⇒ rc=1（与 S2 同构，独立成例）"
seed_baseline
write_mig "0099_sbx_bad_drop.py" "" 'op.execute("DROP TABLE demo_t")'
run_release "$R_NEW" >"$LOGDIR/s7b.log" 2>&1
S7B=$?
echo "  rc=$S7B（期望非零）"
[ $S7B -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "破坏性 DDL 命中" "$LOGDIR/s7b.log" && echo "  ✓ 判据接口文本在案" || { echo "  ✗ 未捕获命中文本"; FAILED=$((FAILED + 1)); }
[ $S7B -ne 0 ] && scenario_row 7b "无声明命中" "非零" "$S7B" "PASS" || scenario_row 7b "无声明命中" "非零" "$S7B" "FAIL"

echo "[S7c] contract 但 pair 不在已部署链 ⇒ rc=1（两步走要求跨发布）"
seed_baseline
write_mig "0099_sbx_orphan_c.py" '# EXPAND-CONTRACT: phase=contract pair=0096' \
  'op.execute("DROP TABLE demo_t")'
run_release "$R_NEW" >"$LOGDIR/s7c.log" 2>&1
S7C=$?
echo "  rc=$S7C（期望非零）"
[ $S7C -ne 0 ] || FAILED=$((FAILED + 1))
grep -q "两步走要求" "$LOGDIR/s7c.log" && echo "  ✓ 跨发布语义文本在案" || { echo "  ✗ 未捕获跨发布文本"; FAILED=$((FAILED + 1)); }
[ $S7C -ne 0 ] && scenario_row 7c "pair未上产" "非零" "$S7C" "PASS" || scenario_row 7c "pair未上产" "非零" "$S7C" "FAIL"

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
[ $S7D -ne 0 ] && scenario_row 7d "同名内容变更" "非零" "$S7D" "PASS" || scenario_row 7d "同名内容变更" "非零" "$S7D" "FAIL"

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

echo "[S7f] 首部署（无上一版）⇒ rc=3 门跳过 + 显著告警，发布不阻断"
bash "$HERE/make_sandbox_root.sh" >>"$LOGDIR/s7f-seed.log" 2>&1
run_release "$R_BASE" >"$LOGDIR/s7f.log" 2>&1
S7F=$?
echo "  rc=$S7F（期望零——首部署窗口门不可判定但放行）"
[ $S7F -eq 0 ] || { FAILED=$((FAILED + 1)); tail -20 "$LOGDIR/s7f.log"; }
grep -q "首部署" "$LOGDIR/s7f.log" && echo "  ✓ 首部署告警在案" || { echo "  ✗ 首部署告警缺失"; FAILED=$((FAILED + 1)); }
[ $S7F -eq 0 ] && scenario_row 7f "首部署rc=3" "零" "$S7F" "PASS（告警不阻断）" || scenario_row 7f "首部署rc=3" "零" "$S7F" "FAIL"

echo "[S7g] 门不可用恒红：解释器缺失（rc=127）即使 allow_contract=true ⇒ fail"
seed_baseline
write_mig "0099_sbx_bad_drop.py" "" 'op.execute("DROP TABLE demo_t")'
mv "$ROOT/shared/venv/bin/python" "$ROOT/shared/venv/bin/python.s7g-bak"
run_release "$R_NEW" -e allow_contract=true -e 'contract_reason=S7g 调试期豁免（门不可用须恒红）' >"$LOGDIR/s7g.log" 2>&1
S7G=$?
mv "$ROOT/shared/venv/bin/python.s7g-bak" "$ROOT/shared/venv/bin/python"   # 恢复，不留残沙
echo "  rc=$S7G（期望非零——封闭式补集：127 不在放行集）"
[ $S7G -ne 0 ] || FAILED=$((FAILED + 1))
[ $S7G -ne 0 ] && scenario_row 7g "门不可用恒红" "非零" "$S7G" "PASS（rc=127 恒红）" || scenario_row 7g "门不可用恒红" "非零" "$S7G" "FAIL（逃生门越界豁免了门崩溃！）"

# ---------- 汇总 ----------
echo
echo "=============================================================="
echo " 场景汇总（断言 ansible-playbook 退出码 + 场景态断言）"
echo "=============================================================="
printf '%-4s %-18s %-10s %-8s %s\n' "编号" "场景" "期望" "实际" "判定"
for row in "${ROWS[@]}"; do echo "$row"; done
echo "--------------------------------------------------------------"
if [ $FAILED -eq 0 ]; then
  echo "结果: 全绿（S4 为设计稿 §6 声明的诚实跳过）"
else
  echo "结果: $FAILED 项断言失败（详见 $LOGDIR/）"
fi
exit $FAILED
