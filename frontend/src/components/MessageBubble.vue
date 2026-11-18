<script setup>
import { ref, computed, defineAsyncComponent } from 'vue'
import ResultTable from './ResultTable.vue'
import MessageText from './MessageText.vue'
const ChartCard = defineAsyncComponent(() => import('./ChartCard.vue'))

const props = defineProps({
    msg: { type: Object, required: true },
})

const messageCopyLabel = ref('复制')
const messageTime = computed(() => {
    const raw = props.msg.timestamp
    if (!raw) return ''
    // Historical backend timestamps are UTC without a zone suffix.
    const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw) ? raw : raw + 'Z')
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
})
async function copyMessage() {
    try {
        await navigator.clipboard.writeText(props.msg.content || '')
        messageCopyLabel.value = '已复制'
    } catch { messageCopyLabel.value = '复制失败，请手动选择' }
}
// 单个数值已在正文回答，原始结果留在执行细节中供核对。
const scalarResult = computed(() => {
    const result = props.msg.queryResult
    return !result?.is_truncated && result?.rows?.length === 1 && result.columns?.length === 1
        && typeof result.rows[0][0] === 'number' && Number.isFinite(result.rows[0][0])
})
const showDebug = ref(false)
const copyLabel = ref('复制')
const statusLabels = {
    completed: '协作完成', partial: '部分完成', failed: '执行失败',
    clarification: '需要澄清', approved: '审核通过', unverified: '未通过审核',
    skipped: '已跳过',
}

async function copySql() {
    try {
        await navigator.clipboard.writeText(props.msg.sql || '')
        copyLabel.value = '已复制'
    } catch {
        copyLabel.value = '复制失败，请手动选择'
    }
}
</script>

<template>
    <div class="bubble-row" :class="msg.role">
        <div v-if="msg.role === 'assistant'" class="assistant-avatar" aria-hidden="true">A</div>
        <div class="message-main" tabindex="0">
        <div class="bubble">
            <!-- 加载中状态 -->
            <p v-if="msg.loading" class="loading" role="status">正在整理思路 <span class="loading-dots" aria-hidden="true"><i></i><i></i><i></i></span></p>

            <template v-else>
                <!-- 指代消解提示：问题被改写时展示 -->
                <p v-if="msg.rewrittenQuestion && msg.rewrittenQuestion !== msg.content" class="rewrite-hint">
                    🔄 已理解：{{ msg.rewrittenQuestion }}
                </p>

                <!-- 回答正文（保留换行） -->
                <MessageText v-if="msg.role === 'assistant'" :text="msg.content || ''" />
                <p v-else class="content">{{ msg.content }}</p>

                <ChartCard v-for="chart in msg.visualizations || []" :key="chart.source_id" :chart="chart" />
                <details v-if="msg.visualizations?.length && msg.queryResult?.rows?.length" class="chart-data">
                    <summary>查看数据表</summary>
                    <ResultTable :columns="msg.queryResult.columns" :rows="msg.queryResult.rows" />
                </details>
                <p v-if="msg.queryResult?.is_truncated" class="rewrite-hint">结果已截断，仅展示部分数据。</p>
                <!--查询结果表格-->
                <ResultTable
                    v-if="!msg.visualizations?.length && !scalarResult && msg.queryResult && msg.queryResult.rows.length"
                    :columns="msg.queryResult.columns"
                    :rows="msg.queryResult.rows"
                />

                <!--执行细节（可折叠）-->
                <div v-if="msg.route === 'data' || msg.sql || msg.taskPlan || msg.taskResults?.length || scalarResult" class="debug">
                    <button :aria-expanded="showDebug" @click="showDebug = !showDebug">
                        {{ showDebug ? '收起':'展开' }}执行细节
                    </button>
                    <div v-if="showDebug" class="debug-body">
                        <details v-if="scalarResult && !msg.visualizations?.length"><summary>原始查询结果</summary>
                            <ResultTable :columns="msg.queryResult.columns" :rows="msg.queryResult.rows" />
                        </details>
                        <p v-for="note in msg.visualizationNotes || []" :key="note">{{ note }}</p>
                        <details v-if="msg.visualizations?.length && !msg.queryResult"><summary>图表数据</summary>
                            <div v-for="chart in msg.visualizations" :key="chart.source_id"><p>{{ chart.title }}</p><ResultTable :columns="chart.columns" :rows="chart.rows" /></div>
                        </details>
                        <div v-if="msg.sql" class="sql-details">
                            <div class="sql-head"><span>执行 SQL</span><button @click="copySql">{{ copyLabel }}</button></div>
                            <pre>{{ msg.sql }}</pre>
                        </div>
                        <details v-if="msg.taskPlan || (msg.collaborationStatus && msg.collaborationStatus !== 'single')" class="collaboration">
                            <summary>多智能体协作 · {{ statusLabels[msg.collaborationStatus] || msg.collaborationStatus }}</summary>
                            <p v-if="msg.taskPlan?.assumptions?.length">口径与假设：{{ msg.taskPlan.assumptions.join('；') }}</p>
                            <ol v-if="msg.taskPlan?.tasks?.length">
                                <li v-for="task in msg.taskPlan.tasks" :key="task.task_id">
                                    {{ task.task_id }}：{{ task.question }}
                                    <small v-if="task.depends_on.length">（依赖 {{ task.depends_on.join('、') }}）</small>
                                </li>
                            </ol>
                            <details v-for="task in msg.taskResults || []" :key="task.task_id" class="task-card">
                                <summary>{{ task.task_id }} · {{ statusLabels[task.status] || task.status }} · {{ task.question }}</summary>
                                <p v-if="task.error">{{ task.error }}</p>
                                <pre v-if="task.sql">{{ task.sql }}</pre>
                                <p v-for="(review, i) in task.reviews" :key="i">
                                    审核 {{ i + 1 }}：{{ review.verdict }} {{ review.issues.join('；') }} {{ review.clarification }}
                                </p>
                                <ResultTable v-if="task.query_result" :columns="task.query_result.columns" :rows="task.query_result.rows" />
                                <p v-if="task.query_result?.is_truncated">结果已截断，不能据此确认完整总体。</p>
                                <details v-if="task.attempts?.length">
                                    <summary>SQL 尝试记录（{{ task.attempts.length }}）</summary>
                                    <pre v-for="(attempt, i) in task.attempts" :key="i">{{ attempt.sql }}{{ attempt.error ? '\n' + attempt.error : '' }}</pre>
                                </details>
                            </details>
                        </details>


                        <p v-if="msg.activeSkill">生效技能：{{ msg.activeSkill }}</p>
                        <p v-if="msg.retrievedDocs && msg.retrievedDocs.length">命中字段：{{ msg.retrievedDocs.join('、') }}</p>
                        <div v-if="msg.attempts && msg.attempts.length">
                            <p>SQL尝试记录：</p>
                            <pre v-for="(att,i) in msg.attempts" :key="i">{{ att.sql }}{{ att.error ? '\n❌ ' + att.error : '' }}</pre>
                        </div>
                    </div>
                </div>
            </template>
        </div>
        <div v-if="!msg.loading" class="message-actions">
            <time v-if="messageTime" :datetime="msg.timestamp">{{ messageTime }}</time>
            <button type="button" @click="copyMessage" aria-label="复制消息"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/></svg>{{ messageCopyLabel }}</button>
        </div>
        </div>
    </div>
</template>

<style scoped>
.message-actions { display:flex; align-items:center; gap:12px; height:30px; padding:4px 5px; color:var(--muted); font-size:12px; opacity:0; visibility:hidden; transition:opacity .15s; }
.bubble-row:hover .message-actions,.bubble-row:focus-within .message-actions { opacity:1; visibility:visible; }
.message-main:has(.message-actions) { outline:none; }
.user .message-actions { justify-content:flex-end; }
.message-actions button { display:inline-flex; align-items:center; gap:5px; border:0; background:transparent; border-radius:5px; padding:2px 5px; cursor:pointer; font-size:12px; }
.message-actions button:hover { color:var(--accent); background:#eaf0fb; }
.message-main:focus .message-actions { opacity:1; visibility:visible; }
@media(hover:none) { .message-actions { opacity:1; visibility:visible; } }

.chart-data { margin-top:12px; font-size:14px; color:var(--muted); }.chart-data summary { cursor:pointer; }
.bubble-row { display:flex; align-items:flex-start; gap:13px; margin:0 0 14px; }.bubble-row.user { justify-content:flex-end; }.message-main { min-width:0; max-width:calc(100% - 48px); }.assistant .message-main { flex:1; }.user .message-main { max-width:80%; }
.message-author { font-size:13px; color:var(--muted); margin-bottom:8px; font-weight:500; }.user .message-author { text-align:right; }
.assistant-avatar { position:relative; width:32px; height:34px; flex-shrink:0; margin-top:2px; display:grid; place-content:center; background:#e7eefb; color:var(--accent); border-radius:10px; font-weight:700; font-size:22px; line-height:1; }.assistant-avatar span { position:absolute; right:3px; bottom:2px; }
.bubble { padding:22px 24px; border-radius:15px; line-height:1.6; font-size:16px; overflow-wrap:anywhere; }
.user .bubble { background:#e9f0fd; color:#2d4a79; border:1px solid #deE8fa; padding:14px 20px; border-top-right-radius:5px; }.assistant .bubble { background:white; border:1px solid var(--line); border-top-left-radius:5px; box-shadow:0 2px 8px #233b5803; }
.content { white-space:pre-wrap; margin:0; }.rewrite-hint { font-size:13px; color:var(--muted); margin:0 0 16px; padding-bottom:12px; border-bottom:1px solid var(--line); }.loading { color:var(--muted); margin:0; font-size:15px; display:flex; align-items:center; gap:10px; }
.loading-dots { display:inline-flex; gap:4px; }.loading-dots i { width:4px; height:4px; background:#7d9ccc; border-radius:50%; animation:pulse 1.2s infinite; }.loading-dots i:nth-child(2) { animation-delay:.15s; }.loading-dots i:nth-child(3) { animation-delay:.3s; }@keyframes pulse { 50% { opacity:.25; transform:translateY(-2px); } }
.debug { margin-top:20px; padding-top:14px; border-top:1px solid var(--line); }.debug button { background:none; border:0; color:#6881a2; font-size:13px; cursor:pointer; padding:2px 0; }.debug button:hover { color:var(--accent); }.debug-body { font-size:14px; color:#52647c; margin-top:14px; }.debug-body p { margin:10px 0; }.debug-body pre { background:#f6f8fb; border:1px solid #e8edf4; padding:14px; border-radius:9px; overflow-x:auto; white-space:pre-wrap; overflow-wrap:anywhere; font-family:Consolas,monospace; font-size:13px; line-height:1.7; }.sql-head { display:flex; justify-content:space-between; gap:16px; margin-bottom:8px; font-size:13px; color:var(--muted); }.sql-details { margin:12px 0; }
.collaboration { margin-top:14px; padding:12px 14px; border:1px solid var(--line); border-radius:10px; }.collaboration summary { cursor:pointer; font-size:14px; }.collaboration ol { padding-left:22px; margin-top:10px; }.task-card { margin-top:10px; padding:12px; background:#f8fafc; border-radius:8px; }
@media(max-width:760px) { .bubble-row { gap:8px; margin-bottom:24px; }.bubble { padding:17px 15px; font-size:16px; }.message-main { max-width:calc(100% - 38px); }.user .message-main { max-width:92%; }.assistant-avatar { width:28px; height:30px; font-size:20px; } }
</style>
