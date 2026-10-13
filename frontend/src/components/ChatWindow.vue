<script setup>
import { ref, watch, nextTick } from 'vue'
import MessageBubble from './MessageBubble.vue'

const props = defineProps({
  messages: { type: Array, required: true },
  loading: { type: Boolean, default: false },
  skills: { type: Array, default: () => [] },
  forcedSkill: { type: String, default: '' },
})

// 子组件不直接改父组件状态，而是向上emit事件（Vue单向数据流核心）
const emit = defineEmits(['send', 'update:forcedSkill'])

const input = ref('')
const scrollArea = ref(null)

function submit() {
  const text = input.value.trim()
  if (!text || props.loading) return
  input.value = ''
  emit('send', text)
}

// 新消息到达后自动滚到底部
watch(() => props.messages.length, async () => {
  await nextTick()
  if (scrollArea.value) scrollArea.value.scrollTop = scrollArea.value.scrollHeight
})
</script>

<template>
  <div class="chat-window">
    <div ref="scrollArea" class="messages">
      <p v-if="!messages.length" class="empty">
        👋 你好，我是数据问答Agent。试试问：华东地区上月销售额是多少？
      </p>
      <MessageBubble v-for="(m, i) in messages" :key="i" :msg="m" />
      <MessageBubble v-if="loading" :msg="{ role: 'assistant', loading: true }" />
    </div>

    <div class="input-bar">
      <select :value="forcedSkill" @change="e => emit('update:forcedSkill', e.target.value)">
        <option value="">🤖 自动匹配技能</option>
        <option v-for="s in skills" :key="s.name" :value="s.name">
          {{ s.display_name }} - {{ s.description.slice(0, 20) }}
        </option>
      </select>
      <input v-model="input" placeholder="输入你的问题…" @keyup.enter="submit" />
      <button :disabled="loading" @click="submit">发送</button>
    </div>
  </div>
</template>

<style scoped>
.chat-window { display: flex; flex-direction: column; height: 100%; }
.messages { flex: 1; overflow-y: auto; padding: 16px; }
.empty { color: #9ca3af; text-align: center; margin-top: 40px; }
.input-bar { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #e5e7eb; }
.input-bar input { flex: 1; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 14px; }
.input-bar button { padding: 10px 20px; background: #2563eb; color: #fff; border: none; border-radius: 8px; cursor: pointer; }
.input-bar button:disabled { background: #93c5fd; cursor: not-allowed; }
.input-bar select { padding: 10px 8px; border: 1px solid #d1d5db; border-radius: 8px; background: #fff; }
</style>
