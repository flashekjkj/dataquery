<script setup>
import { ref, computed, onMounted } from 'vue'
import { chat, listSessions, getSession, deleteSession } from './api'
import SessionList from './components/SessionList.vue'
import ChatWindow from './components/ChatWindow.vue'

const sessions = ref([])
const currentSessionId = ref(null) // null = 新对话，后端收到首次提问时自动建会话
const messages = ref([])
const loading = ref(false)
const sidebarOpen = ref(false)
const conversationTitle = computed(() => sessions.value.find(s => s.session_id === currentSessionId.value)?.title || messages.value.find(m => m.role === 'user')?.content || '新对话')

async function refreshSessions() {
  sessions.value = await listSessions()
}

async function newChat() {
  if (loading.value) return
  sidebarOpen.value = false
  currentSessionId.value = null
  messages.value = []
}

async function openSession(id) {
  if (loading.value) return
  sidebarOpen.value = false
  currentSessionId.value = id
  const data = await getSession(id)
  // 后端历史turns转成前端消息结构（历史里没有表格数据，只有sql和answer）
  messages.value = data.turns.map(t => ({
    role: t.role,
    content: t.role === 'assistant' ? (t.answer || t.content || '') : (t.content || ''),
    sql: t.sql || null,
    collaborationStatus: t.collaboration_status,
    taskPlan: t.task_plan,
    taskResults: t.task_results || [],
  }))
}

async function removeSession(id) {
  if (loading.value) return
  const session = sessions.value.find(s => s.session_id === id)
  if (!window.confirm(`确定删除对话“${session?.title || '未命名对话'}”吗？删除后无法恢复。`)) return
  try {
    await deleteSession(id)
  } catch (e) {
    window.alert(`删除失败：${e.message}`)
    return
  }
  if (id === currentSessionId.value) {
    currentSessionId.value = null
    messages.value = []
  }
  refreshSessions()
}

async function send(question) {
  if (loading.value) return
  messages.value.push({ role: 'user', content: question })
  loading.value = true
  try {
    const r = await chat(question, currentSessionId.value, null)
    currentSessionId.value = r.session_id
    messages.value.push({
      role: 'assistant',
      content: r.answer,
      sql: r.final_sql,
      queryResult: r.query_result,
      retrievedDocs: r.retrieved_docs,
      attempts: r.attempts,
      activeSkill: r.active_skill,
      rewrittenQuestion: r.rewritten_question,
      route: r.route,
      collaborationStatus: r.collaboration_status,
      taskPlan: r.task_plan,
      taskResults: r.task_results,
    })
    refreshSessions()
  } catch (e) {
    messages.value.push({ role: 'assistant', content: `❌ 出错了：${e.message}` })
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  refreshSessions()
})
</script>

<template>
  <div class="layout">
    <button v-if="sidebarOpen" class="sidebar-backdrop" aria-label="关闭会话列表" @click="sidebarOpen = false"></button>
    <aside class="sidebar" :class="{ open: sidebarOpen }" @keydown.esc="sidebarOpen = false">
      <div class="brand"><span class="brand-mark" aria-hidden="true">A<span>·</span></span><div><h1>AskData</h1><p>让数据回应你的问题</p></div><button class="close-sidebar" aria-label="关闭会话列表" @click="sidebarOpen = false">×</button></div>
      <SessionList
        :sessions="sessions"
        :disabled="loading"
        :current-session-id="currentSessionId"
        @select="openSession"
        @new="newChat"
        @delete="removeSession"
      />
      <div class="sidebar-footer"><span class="footer-icon" aria-hidden="true">↗</span><div><strong>数据探索，从这里开始</strong><span>提问 · 查询 · 理解</span></div></div>
    </aside>
    <main class="main">
      <ChatWindow
        :messages="messages"
        :loading="loading"
        :title="conversationTitle"
        @menu="sidebarOpen = true"
        @send="send"
      />
    </main>
  </div>
</template>

<style scoped>
.layout { display:flex; height:100vh; height:100dvh; overflow:hidden; }
.sidebar { width:272px; flex-shrink:0; border-right:1px solid var(--line); display:flex; flex-direction:column; background:#f1f4f8; }
.brand { display:flex; align-items:center; gap:11px; padding:27px 22px 21px; }.brand-mark { width:37px; height:40px; display:grid; place-content:center; position:relative; background:#e0e9fa; color:var(--accent); font-size:26px; font-weight:750; border-radius:11px; line-height:1; }.brand-mark span { position:absolute; right:4px; bottom:4px; font-size:26px; }
.brand h1 { font-size:22px; letter-spacing:-.04em; line-height:1.3; }.brand p { font-size:12px; color:var(--muted); margin-top:3px; }
.main { flex:1; min-width:0; display:flex; flex-direction:column; }
.sidebar-footer { display:flex; gap:10px; align-items:center; padding:20px 22px; border-top:1px solid var(--line); }.footer-icon { width:30px; height:30px; background:#e7edf6; border-radius:8px; text-align:center; color:#5c78a4; }.sidebar-footer strong { display:block; font-size:13px; font-weight:500; color:#586b82; }.sidebar-footer div>span { font-size:12px; color:#65778d; }
.sidebar-backdrop,.close-sidebar { display:none; }
@media(max-width:760px) { .sidebar { position:fixed; inset:0 auto 0 0; z-index:20; visibility:hidden; transform:translateX(-100%); transition:transform .2s; }.sidebar.open { visibility:visible; transform:translateX(0); }.sidebar-backdrop { display:block; position:fixed; inset:0; background:#14253b55; border:0; z-index:19; }.close-sidebar { display:block; margin-left:auto; background:none; border:0; font-size:26px; cursor:pointer; } }
</style>
