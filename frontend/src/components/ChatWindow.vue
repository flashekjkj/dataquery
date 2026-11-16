<script setup>
import { ref, watch, nextTick } from 'vue'
import MessageBubble from './MessageBubble.vue'

const props = defineProps({
  messages: { type: Array, required: true },
  loading: { type: Boolean, default: false },
  title: { type: String, default: '新对话' },
})
const emit = defineEmits(['send', 'menu'])
const input = ref('')
const scrollArea = ref(null)
const editor = ref(null)
const examples = [
  { tag: '销售概览', question: '各地区已支付订单的实付金额合计是多少？' },
  { tag: '客户洞察', question: '各等级客户分别有多少人？' },
  { tag: '商品分析', question: '按商品名称统计订单数量，找出前三名。' },
  { tag: '趋势观察', question: '按月统计已支付订单的实付金额。' },
]
function submit() {
  const text = input.value.trim()
  if (!text || props.loading) return
  input.value = ''
  emit('send', text)
}
function onKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    submit()
  }
}
async function chooseExample(question) {
  input.value = question
  await nextTick()
  editor.value?.focus()
}
watch(input, async () => {
  await nextTick()
  if (editor.value) {
    editor.value.style.height = 'auto'
    editor.value.style.height = Math.min(editor.value.scrollHeight, 180) + 'px'
  }
})
watch(() => [props.messages.length, props.loading], async () => {
  await nextTick()
  if (scrollArea.value) scrollArea.value.scrollTop = scrollArea.value.scrollHeight
})
</script>

<template>
  <div class="chat-window">
    <header class="chat-header">
      <button class="menu-btn" aria-label="打开会话列表" @click="emit('menu')">☰</button>
      <div class="header-title"><span class="workspace-label">数据工作台</span><h2>{{ title }}</h2></div>
      <span class="header-brand">ASKDATA <span>／ CHAT</span></span>
    </header>
    <div ref="scrollArea" class="messages" aria-live="polite" aria-relevant="additions">
      <section v-if="!messages.length" class="welcome">
        <div class="welcome-mark" aria-hidden="true"><span></span><span></span><span></span></div>
        <p class="eyebrow">从一个问题开始</p>
        <h1>用自然语言，读懂数据。</h1>
        <p class="welcome-description">描述你关心的指标、时间和范围，<br class="desktop-break" />让 AskData 帮你查询、分析并整理答案。</p>
        <div class="examples-header"><span>试着这样提问</span><small>以电商演示数据为例</small></div>
        <div class="example-grid">
          <button v-for="example in examples" :key="example.tag" class="example-card" @click="chooseExample(example.question)">
            <span class="example-tag">{{ example.tag }} <span aria-hidden="true">↗</span></span>
            <span class="example-question">{{ example.question }}</span>
          </button>
        </div>
      </section>
      <div v-else class="conversation">
        <MessageBubble v-for="(m, i) in messages" :key="i" :msg="m" />
        <MessageBubble v-if="loading" :msg="{ role: 'assistant', loading: true }" />
      </div>
    </div>
    <footer class="composer-area">
      <form class="composer" @submit.prevent="submit">
        <textarea ref="editor" v-model="input" rows="2" maxlength="2000" aria-label="输入数据问题"
          placeholder="想了解哪些数据？输入你的问题…" @keydown="onKeydown"></textarea>
        <div class="composer-bottom"><span>Enter 发送 <span class="hint-divider">·</span> Shift + Enter 换行</span>
          <button class="send-btn" :disabled="loading || !input.trim()" type="submit" :aria-label="loading ? '正在生成回答' : '发送问题'">
            {{ loading ? '思考中' : '发送' }} <span aria-hidden="true">↑</span>
          </button>
        </div>
      </form>
      <p class="composer-note">回答基于查询结果生成，重要结论请结合数据口径核对。</p>
    </footer>
  </div>
</template>

<style scoped>
.chat-window { display:flex; flex-direction:column; height:100%; min-height:0; }
.chat-header { height:82px; flex-shrink:0; display:flex; align-items:center; gap:14px; padding:0 36px; border-bottom:1px solid var(--line); background:var(--surface); }
.header-title { min-width:0; }.workspace-label { font-size:13px; color:var(--muted); letter-spacing:.04em; }
h2 { font-size:16px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.header-brand { margin-left:auto; color:#64748b; font-size:12px; letter-spacing:.15em; flex-shrink:0; }.header-brand span { color:#94a3b8; }
.menu-btn { display:none; background:none; border:0; font-size:21px; padding:6px; }
.messages { flex:1; min-height:0; overflow-y:auto; padding:36px 32px; scroll-behavior:smooth; }
.welcome { max-width:760px; margin:36px auto 20px; }
.welcome-mark { width:55px; height:55px; display:flex; align-items:flex-end; justify-content:center; gap:5px; padding:14px; border:1px solid #dbeafe; border-radius:17px; background:#eff6ff; margin-bottom:24px; }
.welcome-mark span { width:6px; border-radius:3px; background:#3870d8; height:12px; }.welcome-mark span:nth-child(2) { height:24px; }.welcome-mark span:nth-child(3) { height:18px; }
.eyebrow { font-size:14px; color:var(--accent); margin-bottom:10px; font-weight:500; }
h1 { font-size:clamp(27px,3vw,38px); line-height:1.35; letter-spacing:-.04em; font-weight:650; }
.welcome-description { margin-top:16px; color:var(--muted); line-height:1.85; }
.examples-header { display:flex; justify-content:space-between; align-items:center; gap:12px; margin:38px 0 14px; font-size:14px; }.examples-header small { font-size:13px; color:var(--muted); }
.example-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.example-card { text-align:left; background:white; border:1px solid var(--line); border-radius:14px; padding:18px 20px; cursor:pointer; transition:border-color .18s,box-shadow .18s; }
.example-card:hover { border-color:#b7cef2; box-shadow:0 4px 16px #1e3a5f08; }
.example-tag { display:flex; justify-content:space-between; color:var(--accent); font-size:13px; font-weight:600; margin-bottom:10px; }.example-tag span { color:#94a3b8; }
.example-question { display:block; font-size:15px; line-height:1.7; }
.conversation { max-width:920px; margin:0 auto; }
.composer-area { padding:16px 32px 20px; background:linear-gradient(0deg,var(--canvas) 88%,transparent); }
.composer { max-width:920px; margin:0 auto; padding:16px 18px 12px; border:1px solid #d5dfed; border-radius:18px; background:white; box-shadow:0 5px 22px #203c5a07; transition:border-color .15s; }.composer:focus-within { border-color:#92b3ea; }
textarea { display:block; width:100%; min-height:64px; max-height:180px; border:0; resize:none; outline:none; background:transparent; font-size:17px; line-height:1.65; }.composer textarea:focus-visible { outline:none; }textarea::placeholder { color:#8b98aa; }
.composer-bottom { display:flex; justify-content:space-between; align-items:center; gap:12px; color:var(--muted); font-size:13px; margin-top:10px; }.hint-divider { margin:0 6px; }
.send-btn { border:0; border-radius:10px; background:var(--accent); color:white; font-size:14px; font-weight:600; padding:9px 14px; cursor:pointer; display:flex; align-items:center; gap:13px; }.send-btn span { font-size:20px; line-height:1; }.send-btn:hover:not(:disabled) { background:#1d4fba; }.send-btn:disabled { background:#e6edf7; color:#8799b4; cursor:not-allowed; }
.composer-note { text-align:center; font-size:13px; color:#7c899b; margin-top:10px; }
@media(max-width:760px) { .chat-header { padding:0 18px; height:72px; }.menu-btn { display:block; }.header-brand { display:none; }.messages { padding:24px 18px; }.welcome { margin:12px auto; }.example-grid { grid-template-columns:1fr; }.examples-header { margin-top:28px; }.composer-area { padding:12px 14px; }.composer-bottom>span { font-size:12px; }.desktop-break { display:none; } }
</style>
