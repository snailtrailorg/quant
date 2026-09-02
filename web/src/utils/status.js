// wd-20 §2.1 · 状态元数据唯一真相源（禁止页面私加映射；enumZh 并入消灭平行映射）
export const STATUS_META = {
  running:{dot:'success',zh:'运行中'}, stopped:{dot:'neutral',zh:'已停止'},
  pending:{dot:'warn',zh:'待启动'},   error:{dot:'critical',zh:'错误'},
  frozen:{dot:'info',zh:'冻结'},      done:{dot:'neutral',zh:'完成'},
  complete:{dot:'neutral',zh:'完整'}, partial:{dot:'warn',zh:'部分'},
  missing:{dot:'critical',zh:'缺失'}, active:{dot:'neutral',zh:'活跃'},
  submitted:{dot:'warn',zh:'已提交'}, sending:{dot:'warn',zh:'发送中'},
  sent:{dot:'neutral',zh:'已发送'},   failed:{dot:'critical',zh:'失败'},
  success:{dot:'success',zh:'成功'},  idle:{dot:'neutral',zh:'空闲'},
  ok:{dot:'success',zh:'正常'},      warn:{dot:'warn',zh:'告警'},
  stuck:{dot:'critical',zh:'卡死'},  completed:{dot:'neutral',zh:'已完成'},
  terminated:{dot:'neutral',zh:'已终止'}, paused:{dot:'warn',zh:'已暂停'},
}
export const statusMeta = r => STATUS_META[r] || { dot: 'neutral', zh: r }
