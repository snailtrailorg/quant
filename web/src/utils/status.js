// wd-20 §2.1 · 状态元数据唯一真相源（禁止页面私加映射）
// 盲审A-P2-7：zh 文案改走 i18n 键（status.<value>，locales 双份）——en 界面不再显中文；
// dot=色彩语义（红绿只给数据，dot 才是状态色位）
export const STATUS_META = {
  running:{dot:'success'},  stopped:{dot:'neutral'}, pending:{dot:'warn'},   error:{dot:'critical'},
  frozen:{dot:'info'},      done:{dot:'neutral'},    complete:{dot:'neutral'}, partial:{dot:'warn'},
  missing:{dot:'critical'}, active:{dot:'neutral'},  submitted:{dot:'warn'},  sending:{dot:'warn'},
  sent:{dot:'neutral'},     failed:{dot:'critical'}, success:{dot:'success'}, idle:{dot:'neutral'},
  ok:{dot:'success'},       warn:{dot:'warn'},       stuck:{dot:'critical'},  completed:{dot:'neutral'},
  terminated:{dot:'neutral'}, paused:{dot:'warn'},
}
export const statusMeta = (r, t) => {
  const m = STATUS_META[r] || { dot: 'neutral' }
  return { dot: m.dot, zh: t ? t(`status.${r}`) : r }
}
