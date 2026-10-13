<script setup>
defineProps({
    columns: { type: Array, default: () => [] },
    rows: { type: Array, default: () => [] },
})
</script>

<template>
    <div class="result-table">
        <table>
            <thead>
                <tr>
                    <th v-for="col in columns" :key="col">{{ col }}</th>
                </tr>
            </thead>
            <tbody>
                <tr v-for="(row,i) in rows" :key="i">
                    <td v-for="(cell,j) in row" :key="j">{{ cell }}</td>
                </tr>
            </tbody>
        </table>
        <p class="hint">共 {{ rows.length }} 行</p>

    </div>
</template>

<style scoped>
.result-table {overflow-x:auto;}
table {border-collapse: collapse;font-size: 13px;}
th,td {border:1px solid #e5e7eb;padding: 6px 12px;text-align: left;white-space: nowrap;}
.hint {color: #9ca3af;font-size: 12px;margin: 4px 0 0;}
</style>