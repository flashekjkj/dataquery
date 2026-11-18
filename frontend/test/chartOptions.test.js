import { test } from 'node:test'
import assert from 'node:assert/strict'
import { init, use } from 'echarts/core'
import { LineChart, BarChart, PieChart, ScatterChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, AriaComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { chartOptions, formatNumber } from '../src/components/chartOptions.js'

use([LineChart, BarChart, PieChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, AriaComponent, SVGRenderer])
const fixture = { title: '销售额', columns: ['月份', '地区', '金额'],
  rows: [['2026-08', '华东', 10], ['2026-08', '华南', 20], ['2026-09', '华东', 30]], x: 0, y: [2], series: 1 }

test('multi-series preserves missing points and uses straight lines', () => {
  const option = chartOptions({ ...fixture, type: 'line' })
  assert.deepEqual(option.series[1].data, [20, null])
  assert.equal(option.series[0].smooth, false)
  assert.equal(option.series[0].connectNulls, false)
})

test('all chart templates render actual SVG without browser APIs', () => {
  for (const type of ['line', 'bar', 'horizontal_bar', 'pie', 'histogram', 'stacked_bar', 'scatter']) {
    const chart = init(null, null, { renderer: 'svg', ssr: true, width: 760, height: 300 })
    try {
      const data = type === 'scatter' ? { columns: ['x', 'y'], rows: [[1, 2], [3, 4]], x: 0, y: [1] } : fixture
      chart.setOption(chartOptions({ ...data, title: '销售额', type }))
      const svg = chart.renderToSVGString()
      assert.match(svg, /<svg/)
      assert.match(svg, /<path/)
      assert.ok(!svg.includes('NaN'), type)
    } finally { chart.dispose() }
  }
})

test('horizontal ranking swaps axes and zero values remain visible', () => {
  const option = chartOptions({ ...fixture, type: 'horizontal_bar' })
  assert.equal(option.xAxis.type, 'value')
  assert.equal(option.yAxis.type, 'category')
  assert.equal(option.yAxis.inverse, true)
  assert.equal(formatNumber(0), '0')
  assert.equal(formatNumber(null), '—')
})
