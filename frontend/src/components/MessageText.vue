<script setup>
import { computed } from 'vue'
import { messageBlocks } from './messageFormat'
const props = defineProps({ text: { type: String, default: '' } })
const blocks = computed(() => messageBlocks(props.text))
</script>

<template>
  <div class="formatted-content">
    <template v-for="(block, index) in blocks" :key="index">
      <pre v-if="block.tag === 'pre'"><code>{{ block.text }}</code></pre>
      <hr v-else-if="block.tag === 'hr'" />
      <component v-else-if="block.items" :is="block.tag">
        <li v-for="(parts, item) in block.items" :key="item"><component v-for="(part, i) in parts" :key="i" :is="part.tag">{{ part.text }}</component></li>
      </component>
      <component v-else :is="block.tag"><component v-for="(part, i) in block.parts" :key="i" :is="part.tag">{{ part.text }}</component></component>
    </template>
  </div>
</template>

<style scoped>
.formatted-content { overflow-wrap:anywhere; line-height:1.6; }
p,ul,ol { margin:0; } ul,ol { padding-left:1.4em; } li { margin:0; }
h4 { font-size:1em; font-weight:650; margin:8px 0 2px; } h4:first-child { margin-top:0; }
code { font-family:Consolas,monospace; font-size:.9em; background:#eef2f8; padding:1px 4px; border-radius:4px; white-space:pre-wrap; }
pre { margin:6px 0; padding:10px 12px; background:#f4f6fa; border:1px solid var(--line); border-radius:8px; overflow-x:auto; }
pre code { padding:0; background:none; white-space:pre; } hr { border:0; border-top:1px solid var(--line); margin:8px 0; }
</style>
