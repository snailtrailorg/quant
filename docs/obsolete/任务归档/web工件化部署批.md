# web 工件化部署批（C 方案，2026-08-30 立项）

> 用户裁定。目标：前端 dist 进 `releases/<id>/web/` + `web` 符号链接原子切——前端获得版本化与回滚能力（现状=手动 rsync 无回滚）。`/data/.../quant/web` 路径不变（nginx root 零改动，nginx 跟随符号链接；index.html 不缓存策略已有）。

## 产出（限定范围）

1. **deploy/playbooks/release.yml**：
   - 新 pre-task：控制机 `npm run build`（web/ 下；~45s；失败=中止）——每次发布都 build（确定性优先，不做指纹跳过）
   - 阶段 2 加专用同步：`web/dist/ → releases/<id>/web/`（**不进 deploy_sync_whitelist**——白名单是 server 四切片+指纹体系；web 独立任务，rsync_opts 同款排除 __pycache__/.*；`--delete` ✓）
   - 阶段 6（flip 后）加：`quant-flip-web <release_id>` 切 web 链接（与 server 链接同 release_id）
   - GC 任务：跳过项除 server 指向+被钉外，**加 web 链接指向项**
2. **deploy/wrappers/quant-flip-web**（新，复制 quant-flip-server 模式）：root:root 755，`/usr/local/sbin/`；严参校验 release_id 正则（同 flip-server）；原子切：`ln -sfn` 不原子 → `ln -s releases/<id>/web <tmp> && mv -T <tmp> /data/.../quant/web`；目标已是目录（首次未迁移）→ 明确报错退出提示跑 bootstrap 迁移步骤
3. **sudoers 模板**（deploy/templates/sudoers-quant-deploy.j2）：白名单加 quant-flip-web 一条（Cmnd_Alias 逐命令，`*` 吃空格教训遵守）
4. **bootstrap**（deploy/scripts/bootstrap_server.sh + bootstrap_staging.sh）：装 quant-flip-web wrapper + 一次性迁移步骤（`/data/.../quant/web` 实目录 → `mv web releases/web-legacy-20260830 && ln -s ... web`；幂等：链接已在=no-op；目录在=迁移）
5. **rollback.yml**：回滚 server 链接后同步切 web 链接至目标旧版本

## 设计要点（评审必读）

- **nginx 零改动**：root 路径不变（web 从目录变链接，nginx 跟随）；SPA chunk 内容 hash+index.html 不缓存（既有）——链接切换瞬间原子，无半新半旧
- **GC 断链防护**：GC 判据加 web 链接 readlink（与 server 同款跳过逻辑）；web 与 server 指向同 release_id（同次发布），一次跳过双链同版
- **staging 无 nginx**：链接照建（路径同构，彩排可断言 `readlink web` 指向新 release）；前端可服务性验证属 prod 阶段（curl https 首页）
- **首次迁移顺序**（prod）：跑增量 bootstrap（装 wrapper+迁移目录）→ 再走 release（否则 flip-web 遇目录报错中止，rescue 回滚——安全默认）
- **npm 依赖假设**：控制机 node_modules 在位（开发机即控制机，今天 build 一直绿）；无 node 时 pre-task 明确报错
- **build 进管道的时长**：+45~60s，发布总预算 20min 内可容


## 双盲审修订(08-30,A"可进 P1×4"+B"P0×2+P1×3",修后实施)
- **A-P1-1/B-P1-4 严参查 web 子目录**:wrapper 预检 `[ -d releases/<id>/web ]`(非母本的查根)——防回滚切到无前端的旧版=全站 404;另 web 为实目录(未迁移)→明确报错引 bootstrap
- **B-P0-1 回滚救火不被阻断**:rollback 的 web flip 放 **server flip+波次+收敛断言之后**,目标无 web/ →保现状+告警(failed_when: false)——主回滚成败先定,web 失败不致命;GC 的 web 跳过防其被删
- **B-P0-2 staging 三分支**:bootstrap 迁移幂等三分支——链接在=no-op/**目录在=迁移**/**不存在=no-op**(首次 release 建链;mv+ln 紧邻毫秒窗,前后 curl 自检)
- **B-P1-3 legacy 形态**:`releases/web-legacy-<ts>/web`(目录体)+touch `.deployed`(防 GC 孤儿删+解析统一)
- **A-P1-3 探针榜**:preflight wrapper 检查(release.yml:135)加 quant-flip-web——漏装在阶段 6 server 已 flip 后才爆=无谓回滚(hbcheck 漏探教训 P1-2 同款)
- **A-P1-4 build 定位**:`delegate_to: localhost`+`run_once`(hosts:all 防每主机一跑),置于交易窗断言后
- **A-P2-a**:临时链 `ln -sfn`(非裸 ln -s)
- **GC web 跳过**:按指向目录本体(dirname(readlink web))判——legacy 形态天然同拦

## 验收

- 彩排（staging.local）：release 绿 + `readlink /data/.../quant/web` = releases/<新id>/web + server/web 双链同版 + GC 后双链指向项均在
- 本地彩排环境先跑一次增量 bootstrap_staging（装新 wrapper）再彩排
- prod：发布后 `curl -s https://quant.snailtrail.cc/ | grep 蜗牛量化交易` 见新品牌；rollback.yml 演练一次后 web 链回旧版
- 回归：server 四切片发布行为零变化（白名单/指纹/波次不动）

## 参考（≤2）
- deploy/wrappers/quant-flip-server（模式母本）+ deploy/playbooks/release.yml 阶段 6/GC 现状
