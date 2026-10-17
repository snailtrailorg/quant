// web/scripts/test-coldiff.mjs —— 批126h：colDiff 纯函数单测（蓝点/恢复默认判定核心）。
// 零新增依赖（node 原生 assert）——仓内前端无 vitest，测试面=构建期脚本通道
// （同 check-locales.mjs 模式）；挂 prebuild（package.json）每构建必跑。
import { strict as assert } from 'node:assert'
import { colOffCount, isColOff } from '../src/utils/colDiff.js'

let n = 0
const t = (name, fn) => { fn(); n++; console.log(`  ✓ ${name}`) }

// —— colOffCount 对称差语义 ——
t('全等=0（默认态）', () =>
  assert.equal(colOffCount(['a', 'b', 'c'], ['a', 'b', 'c']), 0))

t('隐藏 1 列=1', () =>
  assert.equal(colOffCount(['a', 'b', 'c'], ['a', 'c']), 1))

t('隐藏 2 列=2（计数回显 N 的来源）', () =>
  assert.equal(colOffCount(['a', 'b', 'c', 'd'], ['a']), 3))

t('开出 1 个默认隐藏列=1', () =>
  assert.equal(colOffCount(['a', 'b'], ['a', 'b', 'hidden1']), 1))

t('换 1 列（隐藏 1+开出 1）=2（对称差非交集差）', () =>
  assert.equal(colOffCount(['a', 'b', 'c'], ['a', 'b', 'x']), 2))

t('全隐藏（空档）=全默认数（P2-5 立法：空档=有效选择，偏离数照实计）', () =>
  assert.equal(colOffCount(['a', 'b', 'c'], []), 3))

t('空默认+空可见=0', () =>
  assert.equal(colOffCount([], []), 0))

t('null/undefined 容错=按空集算（防御）', () => {
  assert.equal(colOffCount(['a', 'b'], null), 2)
  assert.equal(colOffCount(null, ['x']), 1)
})

// —— isColOff 蓝点显隐 ——
t('蓝点：默认态不亮', () =>
  assert.equal(isColOff(['a'], ['a']), false))

t('蓝点：隐藏即亮（偏离=有状态）', () =>
  assert.equal(isColOff(['a', 'b'], ['a']), true))

t('蓝点：恢复默认后灭', () => {
  assert.equal(isColOff(['a', 'b'], ['a']), true)       // 隐藏 b ⇒ 亮
  assert.equal(isColOff(['a', 'b'], ['a', 'b']), false) // 恢复 ⇒ 灭
})

console.log(`colDiff 单测 ${n} 项全过`)
