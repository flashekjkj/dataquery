<script setup>
defineProps({
  disabled: { type: Boolean, default: false },
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: null },
})
const emit = defineEmits(['select', 'new', 'delete'])
</script>

<template>
  <div class="session-list">
    <button class="new-btn" :disabled="disabled" @click="emit('new')">＋ 新对话</button>

    <p class="section-label">最近对话</p>
    <div
      v-for="s in sessions"
      :key="s.session_id"
      class="session-item"
      :class="{ active: s.session_id === currentSessionId }"
      :tabindex="disabled ? -1 : 0" role="button" :aria-disabled="disabled"
      @keydown.enter.self="!disabled && emit('select', s.session_id)"
      @keydown.space.self.prevent="!disabled && emit('select', s.session_id)"
      @click="!disabled && emit('select', s.session_id)"
    >
      <div class="session-main">
        <p class="title">{{ s.title }}</p>
        <p class="meta">{{ s.turn_count }} 轮 · {{ (s.updated_at || '').slice(0, 16).replace('T', ' ') }}</p>
      </div>
      <!-- @click.stop：点删除不触发行选中 -->
      <button class="del-btn" :disabled="disabled" :aria-label="`删除对话：${s.title}`" title="删除对话" @click.stop="emit('delete', s.session_id)">✕</button>
    </div>

    <p v-if="!sessions.length" class="empty">暂无历史会话</p>
  </div>
</template>

<style scoped>
.session-list { display:flex; flex-direction:column; flex:1; min-height:0; overflow-y:auto; padding:0 12px 20px; }
.new-btn { margin:4px 2px 25px; padding:12px 15px; text-align:left; background:white; color:var(--accent); border:1px solid #d6e1f1; border-radius:11px; cursor:pointer; font-size:15px; font-weight:600; box-shadow:0 2px 5px #203c5a03; }.new-btn:hover { border-color:#a7bfe5; }
.section-label { font-size:13px; color:#65778d; padding:0 12px 10px; }
.session-item { display:flex; align-items:center; gap:4px; padding:11px 12px; margin-bottom:5px; cursor:pointer; border-radius:10px; border:1px solid transparent; }.session-item:hover { background:#e9eef5; }.session-item.active { background:#e4edfc; border-color:#d5e2f7; }
.session-main { flex:1; min-width:0; }.title { margin:0; font-size:15px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:#42536b; }.active .title { color:#2656a6; font-weight:600; }.meta { margin:4px 0 0; font-size:13px; color:#65778d; }
.del-btn { flex-shrink:0; width:28px; height:32px; border:0; border-radius:7px; background:none; color:#8a98ad; cursor:pointer; font-size:14px; }.del-btn:hover { color:#c04444; background:#fceaea; }
.empty { color:#65778d; font-size:14px; text-align:center; margin:20px 0; }
button:disabled,.session-item[aria-disabled=true] { opacity:.55; cursor:not-allowed; }
</style>
