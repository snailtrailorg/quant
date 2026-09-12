/**
 * 批17 17A：el-table-v2 列宽拖拽+持久化（EP v2 无原生拖拽——表头手柄自实现）。
 *
 * 用法（v2 三表：Risk 日志 / Reconcile 差异 / SymbolManage）：
 *   const { withWidths } = useV2ColWidths('risk-log')
 *   const columns = computed(() => withWidths(baseCols))
 * withWidths 做三件事：
 *   ① overrides（用户拖过的宽）叠到各列 width 上；② 记录 liveWidths（拖拽起点真值）；
 *   ③ 注入 headerCellRenderer——标题 + 右缘 6px 拖拽手柄（调用方零头部件代码）。
 * 存储：localStorage['colw.<key>'] = {colKey: px}（与 v1 TableShell 同命名空间同语义）。
 */
import { ref, h } from 'vue'

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
    const eff = (Number.isFinite(ov) && ov >= 60) ? ov : c.width
    liveWidths.set(c.key, eff)
    return {
      ...c, width: eff,
      headerCellRenderer: () => h('div', { style: 'position:relative;display:flex;align-items:center;width:100%' }, [
        h('span', { style: 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, String(c.title ?? c.key)),
        h('span', {
          style: 'position:absolute;right:0;top:0;width:6px;height:100%;cursor:col-resize;z-index:2',
          onMousedown: e => startDrag(e, c.key),
        }),
      ]),
    }
  })

  function startDrag(e, key) {
    e.preventDefault(); e.stopPropagation()
    const startX = e.clientX
    const startW = liveWidths.get(key) || 120
    const move = (ev) => {
      const w = Math.max(60, Math.round(startW + ev.clientX - startX))
      overrides.value = { ...overrides.value, [key]: w }
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      persist()
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  return { withWidths }
}
