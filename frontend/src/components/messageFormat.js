// Render supported Markdown as Vue text nodes, never as model-supplied HTML.
export function inlineParts(text) {
  const parts = []
  const pattern = /(`+)([^`\n]+?)\1|\*\*(.+?)\*\*|__(.+?)__|\*([^*\n]+)\*/g
  let offset = 0
  for (const match of text.matchAll(pattern)) {
    if (match.index > offset) parts.push({ tag: 'span', text: text.slice(offset, match.index) })
    parts.push({ tag: match[2] ? 'code' : match[3] || match[4] ? 'strong' : 'em', text: match[2] || match[3] || match[4] || match[5] })
    offset = match.index + match[0].length
  }
  if (offset < text.length) parts.push({ tag: 'span', text: text.slice(offset) })
  return parts
}

export function messageBlocks(text = '') {
  const blocks = []
  let fence = null, code = [], list = null
  for (const line of text.replace(/\r\n/g, '\n').split('\n')) {
    const marker = line.match(/^\s*(`{3,}|~{3,})/)
    if (fence) {
      if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length) {
        blocks.push({ tag: 'pre', text: code.join('\n') }); fence = null; code = []
      } else code.push(line)
      continue
    }
    if (marker) { fence = marker[1]; list = null; continue }
    if (!line.trim()) { list = null; continue }
    const item = line.match(/^\s*(?:([-+*])|\d+[.)、])\s+(.+)$/)
    if (item) {
      const tag = item[1] ? 'ul' : 'ol'
      if (!list || list.tag !== tag) { list = { tag, items: [] }; blocks.push(list) }
      list.items.push(inlineParts(item[2])); continue
    }
    list = null
    const heading = line.match(/^\s*#{1,6}\s+(.+?)\s*#*$/)
    if (/^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)) { blocks.push({tag:'hr'}); continue }
    blocks.push({ tag: heading ? 'h4' : 'p', parts: inlineParts(heading ? heading[1] : line) })
  }
  if (fence) blocks.push({ tag: 'pre', text: code.join('\n') })
  return blocks
}
