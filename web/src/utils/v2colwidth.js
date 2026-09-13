/**
 * 批17 17A：el-table-v2 列宽拖拽+持久化（EP v2 无原生拖拽——表头手柄自实现）。
 *
 * 用法（v2 三表：Risk 日志 / Reconcile 差异 / SymbolManage）：
 *   const { withWidths } = useV2ColWidths('risk-log')
 *   const columns = computed(() => withWidths(baseCols))
 * withWidths 做三件事：
 *   ① overrides（用户拖过的宽）叠到各列 width 上；② 记录 liveWidths（拖拽起点真值）；
 *   ③ 注入 headerCellRenderer——标题 + 右缘 6px 拖拽手柄（调用方零头部件代码）。
 * 盲审A-P1-2 修：override 命中时清 flexGrow/minWidth（EP calcColumnStyle 对 flexGrow 列
 *   以 flex 分配为准、width 仅 basis——不清则拖了被拉回=死手柄；清后转固定宽语义，拖拽真生效）。
 * 盲审A-P2-7 修：mousemove rAF 节流（长表每帧全量重渲）/ e.button 过滤（仅左键起拖）/
 *   onScopeDispose 清理（拖拽中卸载组件不再悬挂 window 监听）。
 * 存储：localStorage['colw.<key>'] = {colKey: px}（与 v1 TableShell 同命名空间同语义）。
 */
import { ref, h, onScopeDispose } from 'vue'

export function useV2ColWidths(storageKey) {
  const storeId = 'colw.' + storageKey
  const overrides = ref(load())
  const liveWidths = new Map()

  function load() {
    try {
      const m = JSON.parse(localStorage.getItem(storeId) || '{}')
      return (m && typeof m === 'object') ? m : {}
    } catch { return {} }
  }
  const persist = () => localStorage.setItem(storeId, JSON.stringify(overrides.value))

  const withWidths = (cols) => cols.map(c => {
    const ov = overrides.value[c.key]
    const has = (Number.isFinite(ov) && ov >= 60)
    const eff = has ? ov : c.width
    liveWidths.set(c.key, eff)
    return {
      ...c, width: eff,
      ...(has ? { flexGrow: undefined, minWidth: undefined } : {}),   // 转固定宽（盲审A-P1-2）
      headerCellRenderer: () => h('div', { style: 'position:relative;display:flex;align-items:center;width:100%' }, [
        h('span', { style: 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, String(c.title ?? c.key)),
        h('span', {
          style: 'position:absolute;right:0;top:0;width:6px;height:100%;cursor:col-resize;z-index:2',
          onMousedown: e => startDrag(e, c.key),
        }),
      ]),
    }
  })

  let cleanup = null
  function startDrag(e, key) {
    if (e.button !== 0) return   // 仅左键（盲审A-P2-7②）
    e.preventDefault(); e.stopPropagation()
    const startX = e.clientX
    const startW = liveWidths.get(key) || 120
    let raf = 0
    const apply = (ev) => {
      if (raf) return
      raf = requestAnimationFrame(() => {   // rAF 节流（盲审A-P2-7①）
        raf = 0
        const w = Math.max(60, Math.round(startW + ev.clientX - startX))
        overrides.value = { ...overrides.value, [key]: w }
      })
    }
    const move = (ev) => apply(ev)
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      if (raf) { cancelAnimationFrame(raf); raf = 0 }
      persist()
      cleanup = null
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    cleanup = up
  }
  onScopeDispose(() => cleanup?.())   // 拖拽中卸载不悬挂（盲审A-P2-7③）

  return { withWidths }
}
