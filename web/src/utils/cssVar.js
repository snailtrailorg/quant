// canvas 图表色解析：ECharts CanvasRenderer 不解析 CSS var()——zrender 把 color 字符串
// 直传 ctx.fillStyle/strokeStyle，`var(--x)` 非法值被静默丢弃、颜色回退默认（wd-20 批二盲审实锤）。
// 约定：echarts option 里的色一律 cssVar('--up') 解成实值再进 option；CSS 上下文
// （style="color:var(--up)" / class）仍可直接写 var()。每次调用实时读 computed style，
// 主题切换后图表 option 重建即取新值（无缓存；option 为 computed 惰性求值，仅浏览器端执行）。
export const cssVar = (name) => {
  if (typeof window === 'undefined') return ''
  const v = getComputedStyle(document.documentElement).getPropertyValue(name)
  return v ? v.trim() : ''
}
