# 批 93 · cron 人读渲染层

> 立项：2026-10-04 深夜。状态：实现完成。
> 用户裁定：**库仍存 cron 作机器真源**（不换结构化存库——croniter 是硬依赖，
> `scheduler/tasks.py:744` 用它解析触发）；只把「看/编」两层翻译成人话。

## 一、问题

数据管理界面 24 行 sync 配置的「调度」列直接渲染 cron 原文（`*/5 * * * *`、
`30 16 * * 1-5`），编辑框也是自由文本 cron input——除作者外没人看得懂。

## 二、契约

| 项 | 契约 |
|---|---|
| 机器真源 | `sync_config.schedule` 一律 cron 字符串，**不变** |
| 人读渲染 | `web/src/utils/cronText.js::describeCron(expr, t)`：`*/5 * * * *`→每 5 分钟、`30 16 * * 1-5`→工作日 16:30、`0 9 * * 1`→每周一 09:00、`0 9 1 1 *`→每年 1 月 1 日 09:00；解析不了**原样返回**（绝不编造） |
| 结构化编辑 | `cronToModel/modelToCron`：interval（`*/N * * * *`）与 scheduled（时间+重复日，空=每天）；**raw 档兜底**——表达式超出图形表达范围时退回原文编辑，绝不锁死用户 |
| 往返一致 | 对库内现存全部 8 种形态**字节级**往返一致（不加前导零、1~5 连续段写 `1-5`、周日归一 0） |
| 编辑框 | 频率 radio（按时刻/按间隔）+ 时间 `el-time-select` + 重复日多选 + 「编译结果」预览行 |
| 词条 | zh/en 对称新增 17 键（dataManage 7 + cronText 10）；`cronSchedule` 文案改「调度/Schedule」 |

## 三、产出

- `web/src/utils/cronText.js`（新）：parseCronFields / cronToModel / modelToCron / describeCron
- `web/src/views/DataManage.vue`：调度列人读主行 + cron 原文次行（等宽/次级色，令牌变量）；
  编辑弹窗结构化选择器 + raw 兜底 + 编译预览
- `web/src/locales/index.js`：zh/en 各 +17 键

## 四、验收

- node 直测 8 例全部字节级往返一致（`0 9 * * 1-5`↔`0 9 * * 1-5`，非 `00 09`）
- 令牌门四维不涨（PX=39/HEX=18/FS=0/BTN=21）；词条门 zh/en 1820 键对称 + 占位符一致
- vite build 通过
