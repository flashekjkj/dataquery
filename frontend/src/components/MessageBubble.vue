<script setup>
import { ref } from 'vue'
import ResultTable from './ResultTable.vue'

const props = defineProps({
    msg: { type: Object, required: true },
})

const showDebug = ref(false)

function copySql() {
    navigator.clipboard.writeText(props.msg.sql || '')
}
</script>

<template>
    <div class="bubble-row" :class="msg.role">
        <div class="bubble">
            <!-- 加载中状态 -->
            <p v-if="msg.loading" class="loading">思考中...</p>
            
            <template v-else>
                <!-- 指代消解提示：问题被改写时展示 -->
                <p v-if="msg.rewrittenQuestion && msg.rewrittenQuestion !== msg.content" class="rewrite-hint">
                    🔄 已理解：{{ msg.rewrittenQuestion }}   
                </p>

                <!-- 回答正文（保留换行） -->
                <p class="content">{{ msg.content }}</p>
                
                <!--SQL卡片+复制按钮-->
                <div v-if="msg.sql" class="sql-card">
                    <div class="sql-head">
                        <span>SQl</span>
                        <button @click="copySql">复制</button>
                    </div>
                    <pre>{{ msg.sql }}</pre>
                </div>

                <!--查询结果表格-->
                <ResultTable
                    v-if="msg.queryResult && msg.queryResult.rows.length"
                    :columns="msg.queryResult.columns"
                    :rows="msg.queryResult.rows" 
                />

                <!--执行细节（可折叠）-->
                <div v-if="msg.route === 'data'" class="debug">
                    <button @click="showDebug = !showDebug">
                        {{ showDebug ? '收起':'展开' }}执行细节
                    </button>
                    <div v-if="showDebug" class="debug-body">
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
    </div>
</template>

<style scoped>
.bubble-row {display: flex; margin: 8px 0;}
.bubble-row.user {justify-content: flex-end;}
.bubble-row.assistant {justify-content: flex-start;}
.bubble {
    max-width: 85%;
    padding: 10px 14px;
    border-radius: 12px;
    line-height: 1.6;
    font-size: 14px;
}
.user .bubble {background: #2563eb;color: #fff;}
.assistant .bubble {background: #f3f4f6;}
.content { white-space: pre-wrap; margin: 0; }
.rewrite-hint { font-size: 12px; color: #6b7280; margin: 0 0 6px; }
.loading { color: #6b7280; margin: 0; }
.sql-card { background: #1f2937; color: #e5e7eb; border-radius: 8px; padding: 8px 10px; margin: 8px 0; }
.sql-head { display: flex; justify-content: space-between; font-size: 12px; color: #9ca3af; margin-bottom: 6px; }
.sql-head button { background: #374151; color: #e5e7eb; border: none; border-radius: 4px; padding: 2px 8px; cursor: pointer; }
.sql-card pre { margin: 0; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
.debug button { background: none; border: none; color: #6b7280; font-size: 12px; cursor: pointer; padding: 0; }
.debug-body { font-size: 12px; color: #4b5563; }
.debug-body pre { background: #fff; padding: 6px; border-radius: 6px; overflow-x: auto; }
</style>