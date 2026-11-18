const colors = ['#3c71d6', '#36a29a', '#9374c7', '#d49b45', '#d17487', '#6e91ab']
export const formatNumber = value => value == null ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)

export function chartOptions(chart) {
  const { rows, columns, x, y, series, type } = chart
  const base = {
    color: colors,
    animationDuration: 300,
    textStyle: { fontFamily: 'inherit', fontSize: 13, color: '#63748b' },
    tooltip: { trigger: 'axis', renderMode: 'richText', confine: true },
    aria: { enabled: true, description: chart.title },
    grid: { left: 16, right: 22, top: 32, bottom: 12, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
  }
  if (type === 'pie') return {
    ...base,
    tooltip: { trigger: 'item', renderMode: 'richText', confine: true },
    legend: { bottom: 0, type: 'scroll', icon: 'circle', textStyle: { color: '#63748b' } },
    series: [{ type: 'pie', radius: ['45%', '69%'], center: ['50%', '44%'],
      itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 4 },
      label: { show: false }, data: rows.map(row => ({ name: String(row[x]), value: row[y[0]] })) }],
  }
  const valueAxis = { type: 'value', name: chart.unit, splitLine: { lineStyle: { color: '#edf1f6' } },
    axisLabel: { formatter: value => Math.abs(value) >= 10000 ? `${formatNumber(value / 10000)}万` : formatNumber(value) } }
  if (type === 'scatter') return {
    ...base, tooltip: { trigger: 'item', renderMode: 'richText', confine: true },
    xAxis: { ...valueAxis, name: columns[x] }, yAxis: { ...valueAxis, name: columns[y[0]] },
    series: [{ type: 'scatter', symbolSize: 9, data: rows.filter(row => row[x] != null && row[y[0]] != null).map(row => [row[x], row[y[0]]]) }],
  }
  const labels = [...new Set(rows.map(row => String(row[x])))]
  const categoryAxis = { type: 'category', data: labels, axisTick: { show: false },
    axisLine: { lineStyle: { color: '#e5eaf1' } }, axisLabel: { hideOverlap: true, width: 100, overflow: 'truncate' } }
  const groups = series == null ? [null] : [...new Set(rows.map(row => row[series]))]
  const traces = groups.map(group => {
    const values = new Map(rows.filter(row => series == null || row[series] === group).map(row => [String(row[x]), row[y[0]]]))
    return { name: group == null ? columns[y[0]] : String(group), type: type === 'line' ? 'line' : 'bar',
      data: labels.map(label => values.get(label) ?? null),
      ...(type === 'line' ? { smooth: false, connectNulls: false, showSymbol: rows.length < 25, symbolSize: 6, lineStyle: { width: 2.5 } } :
        { barMaxWidth: 34, itemStyle: { borderRadius: type === 'horizontal_bar' ? [0, 4, 4, 0] : [4, 4, 0, 0] } }),
      ...(type === 'stacked_bar' ? { stack: 'total' } : {}),
    }
  })
  return { ...base, legend: { show: series != null, top: 0, type: 'scroll', textStyle: { color: '#63748b' } },
    xAxis: type === 'horizontal_bar' ? valueAxis : categoryAxis,
    yAxis: type === 'horizontal_bar' ? { ...categoryAxis, inverse: true } : valueAxis, series: traces }
}

// Normalize legacy question titles as well as newly generated chart titles.
export function chartTitle(chart) {
  const raw = (chart.title || '').replace(/^#+\s*/, '').trim()
  const names = { line: '趋势', bar: '对比', horizontal_bar: '排名', pie: '占比', stacked_bar: '构成', histogram: '分布', scatter: '相关性', card: '概览' }
  let title = raw.replace(/[？?。！!]+$/g, '').replace(/^(请问|请|帮我|查看|展示|统计)/, '').replace(/(分别)?(是多少|有多少|有哪些|如何变化|怎么样|是什么)$/, '').trim()
  if (!title) title = '查询结果'
  const suffix = names[chart.type] || '分析'
  if (!new RegExp('趋势|变化|对比|排名|排行|占比|构成|分布|相关性|概览|合计|总额').test(title)) title += suffix
  return title
}
