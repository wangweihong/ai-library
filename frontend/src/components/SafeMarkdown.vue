<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import type { ResourceFile, ResourceKind } from '../resources/types'
const props = defineProps<{ text: string; kind?: ResourceKind; resourceId?: number; number?: number; currentPath?: string; files?: ResourceFile[] }>()
const emit = defineEmits<{ file: [path: string] }>()
function localPath(url: string): string | null {
  if (/^(?:[a-z][a-z\d+.-]*:|\/\/|\/|#)/i.test(url)) return null
  let decoded: string
  try { decoded = decodeURIComponent(url.split('#')[0]) } catch { return null }
  const parts = (props.currentPath || '').split('/').slice(0, -1)
  for (const part of decoded.split('/')) {
    if (part === '..') { if (!parts.length) return null; parts.pop() }
    else if (part && part !== '.') parts.push(part)
  }
  const path = parts.join('/')
  return props.files?.some(f => f.path === path) ? path : null
}
const html = computed(() => {
  const md = new MarkdownIt({ html: false, linkify: false, breaks: false })
  md.renderer.rules.image = (tokens, idx) => {
    const token = tokens[idx], path = localPath(token.attrGet('src') || '')
    const file = props.files?.find(f => f.path === path)
    if (!file || !/^image\/(png|jpeg|webp|gif)$/.test(file.media_type) || !props.resourceId) {
      return `<span class="muted">[图片：${md.utils.escapeHtml(token.content || token.attrGet('src') || '')}]</span>`
    }
    const url = `/api/v1/${props.kind}/${props.resourceId}/files/${path!.split('/').map(encodeURIComponent).join('/')}?number=${props.number}`
    return `<img src="${md.utils.escapeHtml(url)}" alt="${md.utils.escapeHtml(token.content)}" loading="lazy">`
  }
  const original = md.renderer.rules.link_open || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
  md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    const path = localPath(tokens[idx].attrGet('href') || '')
    if (path) { tokens[idx].attrSet('data-library-file', path); tokens[idx].attrSet('href', '#') }
    else { tokens[idx].attrSet('target', '_blank'); tokens[idx].attrSet('rel', 'noopener noreferrer') }
    return original(tokens, idx, options, env, self)
  }
  return DOMPurify.sanitize(md.render(props.text), { USE_PROFILES: { html: true } })
})
function click(event: MouseEvent) {
  const link = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[data-library-file]')
  if (link) { event.preventDefault(); emit('file', link.dataset.libraryFile!) }
}
</script>
<template><div class="markdown-body" @click="click" v-html="html"></div></template>
