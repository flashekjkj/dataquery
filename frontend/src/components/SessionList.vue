<script setup>
defineProps({
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: null },
})
const emit = defineEmits(['select', 'new', 'delete'])
</script>

<template>
  <div class="session-list">
    <button class="new-btn" @click="emit('new')">＋ 新对话</button>

    <div
      v-for="s in sessions"
      :key="s.session_id"
      class="session-item"
      :class="{ active: s.session_id === currentSessionId }"
      @click="emit('select', s.session_id)"
    >
      <div class="session-main">
        <p class="title">{{ s.title }}</p>
        <p class="meta">{{ s.turn_count }} 轮 · {{ (s.updated_at || '').slice(0, 16).replace('T', ' ') }}</p>
      </div>
      <!-- @click.stop：点删除不触发行选中 -->
      <button class="del-btn" @click.stop="emit('delete', s.session_id)">✕</button>
    </div>

    <p v-if="!sessions.length" class="empty">暂无历史会话</p>
  </div>
</template>

<style scoped>
.session-list { display: flex; flex-direction: column; height: 100%; overflow-y: auto; }
.new-btn { margin: 12px; padding: 10px; background: #2563eb; color: #fff; border: none; border-radius: 8px; cursor: pointer; }
.session-item { display: flex; align-items: center; padding: 10px 12px; cursor: pointer; border-bottom: 1px solid #f3f4f6; }
.session-item:hover { background: #f9fafb; }
.session-item.active { background: #eff6ff; }
.session-main { flex: 1; min-width: 0; }
.title { margin: 0; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.meta { margin: 2px 0 0; font-size: 11px; color: #9ca3af; }
.del-btn { background: none; border: none; color: #d1d5db; cursor: pointer; }
.del-btn:hover { color: #ef4444; }
.empty { color: #9ca3af; font-size: 13px; text-align: center; margin-top: 20px; }
</style>
