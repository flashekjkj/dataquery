// 后端接口统一封装：所有请求走 Vite 代理的 /api 前缀
const BASE = '/api'

//统一处理：非2xx时抛出带后端的detail的错误
async function request(path, options = {}) {
    const resp = await fetch(`${BASE}${path}`, options)
    if (!resp.ok) {
        let detail = resp.statusText
        try {
            const body = await resp.json()
            if (body.detail) detail = body.detail
        } catch {/*响应不是JSON时忽略*/ }
        throw new Error(detail)
    }
    return resp.json()
}

//提问（非流式）：返回完整结果（answer/sql/表格/命中字段/尝试记录）
export function chat(question, sessionId = null, forcedSkill = null) {
    return request('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            question,
            session_id: sessionId,
            forced_skill:forcedSkill,
        }),
    })
}

//会话列表
export function listSessions() {
    return request('/sessions')
}

//单个会话的完整记录
export function getSession(sessionId) {
    return request(`/sessions/${sessionId}`)
}

//删除会话
export function deleteSession(sessionId) {
    return request(`/sessions/${sessionId}`,{method:'DELETE'})
}

//技能列表
export function listSkills() {
    return request('/skills')
}

//流式提问（SSE）：每收到一个事件就回调onEvent，结束时返回session_id
export async function chatStream(question,sessionId,forcedSkill,onEvent) {
    const resp = await fetch(`${BASE}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            question,
            session_id: sessionId,
            forced_skill:forcedSkill,
        }),
    })
    if (!resp.ok || !resp.body) throw new Error(`请求失败：${resp.status}`)
    
    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let finalSessionId = null

    while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        //SSE事件之间以空行分隔，逐条解析
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop()
        for (const chunk of chunks) {
            for (const line of chunk.split('\n')) {
                if (!line.startsWith('data:')) continue
                const event = JSON.parse(line.slice(6))
                if (event.type === 'done') finalSessionId = event.session_id
                onEvent(event)
            }
        }
    }
    return finalSessionId
}