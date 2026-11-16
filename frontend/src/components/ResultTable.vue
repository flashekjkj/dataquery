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
                    <td v-for="(cell,j) in row" :key="j" :class="{ numeric: typeof cell === 'number' }">{{ cell }}</td>
                </tr>
            </tbody>
        </table>
        <p class="hint">共 {{ rows.length }} 行</p>

    </div>
</template>

<style scoped>
.result-table { overflow-x:auto; margin-top:20px; border:1px solid var(--line); border-radius:10px; }
table { border-collapse:collapse; font-size:15px; width:100%; }th,td { border-bottom:1px solid #edf1f6; padding:11px 16px; text-align:left; white-space:nowrap; }th { background:#f5f8fc; color:#526781; font-weight:600; font-size:13px; }tbody tr:last-child td { border-bottom:0; }tbody tr:hover { background:#fafcff; }.numeric { text-align:right; font-variant-numeric:tabular-nums; color:#345379; }
.hint { color:#65778d; font-size:13px; margin:0; padding:8px 16px; background:#fbfcfe; border-top:1px solid var(--line); }
</style>
