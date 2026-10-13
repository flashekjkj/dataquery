<script setup>
import { ref, onMounted } from 'vue'
import { chat, listSessions, getSession, deleteSession, listSkills } from './api'
import SessionList from './components/SessionList.vue'
import ChatWindow from './components/ChatWindow.vue'

const sessions = ref([])
const currentSessionId = ref(null) // null = 新对话，后端收到首次提问时自动建会话
const messages = ref([])
const loading = ref(false)
const skills = ref([])
const forcedSkill = ref('')

async function refreshSessions() {
  sessions.value = await listSessions()
}

async function newChat() {
  currentSessionId.value = null
  messages.value = []
}

async function openSession(id) {
  currentSessionId.value = id
  const data = await getSession(id)
  // 后端历史turns转成前端消息结构（历史里没有表格数据，只有sql和answer）
  messages.value = data.turns.map(t => ({
    role: t.role,
    content: t.role === 'assistant' ? (t.answer || t.content || '') : (t.content || ''),
    sql: t.sql || null,
  }))
}

async function removeSession(id) {
  await deleteSession(id)
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
    const r = await chat(question, currentSessionId.value, forcedSkill.value || null)
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
  listSkills().then(s => (skills.value = s)).catch(() => {})
})
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <h1 class="logo">AskData</h1>
      <SessionList
        :sessions="sessions"
        :current-session-id="currentSessionId"
        @select="openSession"
        @new="newChat"
        @delete="removeSession"
      />
    </aside>
    <main class="main">
      <ChatWindow
        :messages="messages"
        :loading="loading"
        :skills="skills"
        :forced-skill="forcedSkill"
        @send="send"
        @update:forced-skill="v => (forcedSkill = v)"
      />
    </main>
  </div>
</template>

<style scoped>
.layout { display: flex; height: 100vh; }
.sidebar { width: 260px; border-right: 1px solid #e5e7eb; display: flex; flex-direction: column; }
.logo { font-size: 18px; padding: 16px; margin: 0; }
.main { flex: 1; display: flex; flex-direction: column; }
</style>
