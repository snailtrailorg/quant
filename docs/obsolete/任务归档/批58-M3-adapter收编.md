# 批 58：M3 adapter 收编（_HANDLERS 退役+归置表落地）

> 29 号 §六+§十二归置表（现状 20 项+占位 4）=主规格。内部可拆 58a（K 线+基本面）/58b（三档+静态）两步灰度。

## 目标
全部同步 handler 收编 fetch(kind[,sub_kind]) 统一签名；行为等价 bug-for-bug（preserve_current_normalization=True 开关）；sync_kind_config+restate_event 建表；CI 断言一白名单清零。

## 依赖（就绪）
- 批 57 ✅（resolve 供给模式）
- 归置表 20 项（29 号 §十二冻结版）

## 产出
BaseAdapter.fetch 签名+TushareAdapter kind 分派表+引擎通用循环（盘缺口→resolve(supply)→fetch→落仓→游标）+0097 两表+同步配置页 kind/sub_kind 下拉改造+provider 列可空化+tushare_api 退役+分层映射回写。

## 限定范围
不改数据内容（行为等价生死线）；不动消费方（59）；竞价条并入不在本批（58+独立批）。回补守 28 §8.4 切刀。

## 接口契约
fetch(kind,req,acct)->ContractFrame；归置表 20 行逐项照搬。

## 验收标准
①同交易日全量同步改造前后按 pk 排序全字段精确等值（排除代理键/摄取时间戳/dataset_version）②新增 featured_daily 子类=纯配置行零代码演示 ③CI 断言一白名单清零 ④_HANDLERS 删除后全测试绿。

## mock 方式
pull 函数 mock（任务模板 §二 Tushare 模式）；对照脚本跑真库（行为级）。

## 参考文档
29 号 §六+§十二；28 号 §5.2/§8.4。
