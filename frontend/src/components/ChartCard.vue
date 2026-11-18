<script setup>
import { computed, ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { init, use } from 'echarts/core'
import { LineChart, BarChart, PieChart, ScatterChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, AriaComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { chartOptions, formatNumber, chartTitle } from './chartOptions'

use([LineChart, BarChart, PieChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, AriaComponent, SVGRenderer])
const props = defineProps({ chart: { type: Object, required: true } })
const displayTitle = computed(() => chartTitle(props.chart))
const canvas = ref(null)
const failed = ref(false)
let instance, observer
async function render() {
  await nextTick()
  if (!canvas.value || props.chart.type === 'card') return
  try {
    instance ??= init(canvas.value, null, { renderer: 'svg' })
    instance.setOption(chartOptions({ ...props.chart, title: displayTitle.value }), true)
    instance.resize()
  } catch {
    failed.value = true
  }
}
function download() {
  if (!instance) return
  const link = document.createElement('a')
  link.href = instance.getDataURL({ type: 'svg', backgroundColor: '#ffffff' })
  link.download = displayTitle.value.replace(/[\\/:*?"<>|]/g, '-') + '.svg'
  link.click()
}
onMounted(() => {
  render()
  observer = new ResizeObserver(() => instance?.resize())
  if (canvas.value) observer.observe(canvas.value)
})
watch(() => props.chart, render, { deep: true })
onBeforeUnmount(() => { observer?.disconnect(); instance?.dispose(); instance = null })
</script>

<template>
  <section class="chart-card" :aria-label="displayTitle">
    <div class="chart-heading"><div><h3>{{ displayTitle }}</h3></div>
      <button v-if="chart.type !== 'card' && !failed" @click="download" aria-label="下载图表">下载</button>
    </div>
    <div v-if="chart.type === 'card'" class="indicators">
      <div v-for="index in chart.y" :key="index"><span>{{ chart.columns[index] }}</span><strong>{{ formatNumber(chart.rows[0][index]) }}<small>{{ chart.unit }}</small></strong></div>
    </div>
    <p v-else-if="failed" class="fallback">图表暂时无法显示，可展开下方数据表查看。</p>
    <div v-else ref="canvas" class="chart-canvas" role="img" :aria-label="displayTitle + '，详细数值见下方数据表'" />
  </section>
</template>

<style scoped>
.chart-card { margin-top:16px; padding:19px 18px 12px; border:1px solid var(--line); border-radius:12px; background:var(--surface,white); overflow:hidden; }
.chart-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:12px; }.chart-heading h3 { font-size:16px; font-weight:600; line-height:1.5; margin:0; color:var(--ink); }.chart-heading p { font-size:12px; color:var(--muted); margin:5px 0 12px; line-height:1.6; }.chart-heading button { background:#f4f7fc; color:var(--accent); border:0; padding:5px 10px; border-radius:6px; cursor:pointer; white-space:nowrap; }
.chart-canvas { width:100%; height:300px; }.indicators { display:flex; flex-wrap:wrap; gap:24px; padding:8px 0 14px; }.indicators span { display:block; font-size:13px; color:var(--muted); }.indicators strong { display:block; font-size:30px; font-weight:650; color:var(--accent); font-variant-numeric:tabular-nums; }.indicators small { font-size:13px; margin-left:6px; font-weight:400; }.fallback { font-size:14px; color:var(--muted); }
@media(max-width:760px) { .chart-card { padding:14px 10px 8px; }.chart-canvas { height:260px; margin-bottom:12px; }.chart-heading h3 { font-size:15px; } }
</style>
