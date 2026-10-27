<script setup lang="ts">
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { json, request } from '../api'
import ModalShell from '../components/ModalShell.vue'
import TagEditor from '../components/TagEditor.vue'
import SafeMarkdown from '../components/SafeMarkdown.vue'
import VariableEditor from '../components/VariableEditor.vue'
import { blankResource, resourceInput } from './types'
import type { FileChange, Resource, ResourceFile, ResourceInput, ResourceKind, ResourcePage, ResourceSummary, ResourceVersion, Variable } from './types'
import type { Tag, TagResult } from './prompts/types'

const props = defineProps<{ kind: ResourceKind; theme: string }>()
const emit = defineEmits<{ navigate: [kind: 'prompts' | ResourceKind]; theme: [value: string]; prompt: [value: {text: string; media_type: string}] }>()
const title = computed(() => props.kind === 'templates' ? '提示词模板' : 'Skills')
const api = (suffix = '') => `/${props.kind}${suffix}`
const active = ref(true)
const query = ref(''), search = ref(''), origin = ref('all'), model = ref(''), templateKind = ref(''), untagged = ref(false)
const filterTags = ref<string[]>([]), tags = ref<Tag[]>([]), tagSearch = ref('')
const untaggedCount = ref(0), models = ref<string[]>([]), items = ref<ResourceSummary[]>([])
const total = ref(0), page = ref(1), loading = ref(false), busy = ref(false)
const pages = computed(() => Math.max(1, Math.ceil(total.value / 50)))
const error = ref(''), editorError = ref(''), notice = ref('')
const hover = ref<Resource | null>(null)
let hoverTimer: ReturnType<typeof setTimeout> | undefined
let hoverSerial = 0
function clearHover() { clearTimeout(hoverTimer); ++hoverSerial; hover.value = null }
function showHover(id: number) {
  clearTimeout(hoverTimer); const serial = ++hoverSerial
  hoverTimer = setTimeout(async () => { try { const value = await request<Resource>(api(`/${id}`)); if (serial === hoverSerial) hover.value = value } catch { /* Preview is optional. */ } }, 250)
}
const current = ref<Resource | null>(null), draft = ref<ResourceInput>(blankResource(props.kind))
const creating = ref(false), baseline = ref(''), pendingTag = ref(false)
const changes = ref<Record<string, FileChange>>({})
const tab = ref<'overview' | 'use' | 'edit' | 'files' | 'history'>('overview')
const focused = ref(false), mobileOpen = ref(false), mobileFilters = ref(false)
const dirty = computed(() => (current.value !== null || creating.value) && (pendingTag.value || JSON.stringify(draft.value) !== baseline.value || Object.keys(changes.value).length > 0))
const readonly = computed(() => Boolean(current.value?.builtin))
const tagEditor = ref<InstanceType<typeof TagEditor>>()
const guardOpen = ref(false), deleteOpen = ref(false)
let guarded: (() => void | Promise<void>) | null = null
let listSerial = 0, detailSerial = 0, fileSerial = 0, renderSerial = 0, historySerial = 0, historyFileSerial = 0
let controller: AbortController | undefined
const filePath = ref('SKILL.md'), fileText = ref(''), fileBinary = ref(false), fileLoading = ref(false)
const uploadPrefix = ref('assets')
const clipboardPaths = ref<string[]>([])
const values = ref<Record<string, string | number | null>>({}), rendered = ref(''), renderBusy = ref(false)
const versions = ref<ResourceVersion[]>([]), history = ref<Resource | null>(null), historyText = ref(''), historyPath = ref('')
const historyBusy = ref(false)
const importInput = ref<HTMLInputElement>(), directoryInput = ref<HTMLInputElement>(), attachmentInput = ref<HTMLInputElement>()
const modelText = computed({ get: () => draft.value.target_models.join(', '), set: value => { draft.value.target_models = value.split(/[,，]/).map(s => s.trim()).filter(Boolean) } })
const localFiles = computed(() => {
  const files = new Map((current.value?.files || []).map(file => [file.path, file]))
  for (const change of Object.values(changes.value)) {
    if (change.remove) files.delete(change.path)
    else files.set(change.path, { path: change.path, hash: '', size: change.text === undefined ? Math.floor((change.base64?.length || 0) * .75) : new TextEncoder().encode(change.text).length, media_type: current.value?.files.find(f => f.path === change.path)?.media_type || 'application/octet-stream' })
  }
  if (creating.value && props.kind === 'skills' && !files.has('SKILL.md')) files.set('SKILL.md', { path: 'SKILL.md', hash: '', size: draft.value.body.length, media_type: 'text/markdown' })
  return [...files.values()].sort((a, b) => a.path.localeCompare(b.path))
})
const selectedFile = computed(() => localFiles.value.find(file => file.path === filePath.value))
const isImage = (file?: ResourceFile) => Boolean(file && /^image\/(png|jpeg|gif|webp)$/.test(file.media_type))
const fileUrl = (record: Resource, path: string) => `/api/v1/${props.kind}/${record.id}/files/${path.split('/').map(encodeURIComponent).join('/')}?number=${record.number}`
const imageUrl = computed(() => {
  const pending = changes.value[filePath.value]
  if (pending?.base64 && isImage(selectedFile.value)) return `data:${selectedFile.value!.media_type};base64,${pending.base64}`
  return current.value ? fileUrl(current.value, filePath.value) : ''
})
const message = (exc: unknown) => exc instanceof Error ? exc.message : String(exc)
const date = (value: string) => new Date(value).toLocaleString('zh-CN')
const sourceUrl = (url?: string) => url && /^https?:\/\//i.test(url) ? url : undefined

async function loadList() {
  const serial = ++listSerial
  controller?.abort(); controller = new AbortController(); loading.value = true
  const params = new URLSearchParams({ q: query.value, origin: origin.value, target_model: model.value,
    template_kind: templateKind.value, untagged: String(untagged.value), page: String(page.value), page_size: '50' })
  filterTags.value.forEach(tag => params.append('tags', tag))
  try {
    const result = await request<ResourcePage>(api(`?${params}`), { signal: controller.signal })
    if (serial !== listSerial) return
    items.value = result.items; total.value = result.total; models.value = result.target_models; error.value = ''
    if (page.value > pages.value) page.value = pages.value
  } catch (exc) {
    if (serial === listSerial && !(exc instanceof DOMException && exc.name === 'AbortError')) { error.value = message(exc); items.value = []; total.value = 0 }
  } finally { if (serial === listSerial) loading.value = false }
}
async function loadTags() {
  try { const result = await request<TagResult>(api('/tags')); tags.value = result.items; untaggedCount.value = result.untagged }
  catch (exc) { error.value = message(exc) }
}
watch([query, origin, model, templateKind, untagged, filterTags], () => { page.value = 1; void loadList() }, { deep: true })
watch(page, () => { void loadList() })
watch(values, () => { ++renderSerial; rendered.value = '' }, { deep: true })
function resetFilters() { search.value = ''; query.value = ''; origin.value = 'all'; model.value = ''; templateKind.value = ''; untagged.value = false; filterTags.value = [] }
function toggleTag(tag: string) { filterTags.value = filterTags.value.includes(tag) ? filterTags.value.filter(t => t !== tag) : [...filterTags.value, tag] }
function requestLeave(action: () => void | Promise<void>) {
  if (busy.value) return
  if (dirty.value) { guarded = action; guardOpen.value = true }
  else void action()
}
defineExpose({ requestLeave })
function setRecord(record: Resource | null) {
  current.value = record; creating.value = record === null
  draft.value = record ? resourceInput(record) : blankResource(props.kind)
  changes.value = {}; pendingTag.value = false; baseline.value = JSON.stringify(draft.value)
  editorError.value = ''; rendered.value = ''; history.value = null; historyText.value = ''; versions.value = []
  fileText.value = record?.body || draft.value.body; filePath.value = 'SKILL.md'; fileBinary.value = false
  ++historySerial; ++historyFileSerial; historyBusy.value = false
  clipboardPaths.value = record ? ['SKILL.md', ...record.files.filter(f => (f.path.startsWith('references/') || f.path.startsWith('assets/')) && /\.(md|txt|json|ya?ml|csv)$/.test(f.path)).map(f => f.path)] : ['SKILL.md']
  values.value = Object.fromEntries((record?.variables || []).map(v => [v.name, v.default ?? '']))
  mobileOpen.value = true; ++fileSerial; ++renderSerial
}
async function openRecord(id: number) {
  const serial = ++detailSerial
  try {
    const record = await request<Resource>(api(`/${id}`))
    if (serial !== detailSerial) return
    setRecord(record); tab.value = 'overview'
  } catch (exc) { editorError.value = message(exc) }
}
function newRecord() { requestLeave(() => { ++detailSerial; setRecord(null); tab.value = 'edit' }) }
async function finishGuard(saveFirst: boolean) {
  if (saveFirst && !await save()) return
  const action = guarded; guarded = null; guardOpen.value = false
  if (!saveFirst) {
    draft.value = current.value ? resourceInput(current.value) : blankResource(props.kind)
    changes.value = {}; pendingTag.value = false; baseline.value = JSON.stringify(draft.value)
    fileText.value = draft.value.body
    if (current.value && props.kind === 'skills' && tab.value === 'files') void openFile('SKILL.md')
  }
  await action?.()
}
async function save(): Promise<boolean> {
  if (busy.value || readonly.value) return false
  tagEditor.value?.flush(); await nextTick()
  if (!draft.value.title.trim()) { editorError.value = '请输入资源名称'; return false }
  busy.value = true; editorError.value = ''
  const previousTab = tab.value, previousPath = filePath.value
  try {
    const body = { ...JSON.parse(JSON.stringify(draft.value)), files: Object.values(changes.value).map(file => file.path === 'SKILL.md' && !file.remove ? {path: file.path, text: draft.value.body} : file) }
    const result = current.value ? await request<Resource>(api(`/${current.value.id}`), json('PATCH', { ...body, version: current.value.version }))
      : await request<Resource>(api(), json('POST', body))
    setRecord(result); tab.value = previousTab
    if (props.kind === 'skills' && previousTab === 'files') await openFile(result.files.some(f => f.path === previousPath) ? previousPath : 'SKILL.md')
    notice.value = `已保存 · v${result.number}`
    await Promise.all([loadList(), loadTags()]); return true
  } catch (exc) { editorError.value = message(exc); return false }
  finally { busy.value = false }
}
async function copyResource(number?: number) {
  if (!current.value) return
  busy.value = true; editorError.value = ''
  try {
    const record = await request<Resource>(api(`/${current.value.id}/copy`), json('POST', { version: current.value.version, ...(number === undefined ? {} : { number }) }))
    setRecord(record); tab.value = 'edit'; notice.value = '已创建独立副本，原预设保持只读'
    await Promise.all([loadList(), loadTags()])
  } catch (exc) { editorError.value = message(exc) }
  finally { busy.value = false }
}
async function removeRecord() {
  if (!current.value) return
  busy.value = true
  try {
    await request(api(`/${current.value.id}?version=${encodeURIComponent(current.value.version)}`), { method: 'DELETE' })
    current.value = null; creating.value = false; changes.value = {}; pendingTag.value = false
    deleteOpen.value = false; mobileOpen.value = false; notice.value = '资源及其历史已删除'
    await Promise.all([loadList(), loadTags()])
  } catch (exc) { editorError.value = message(exc) }
  finally { busy.value = false }
}
async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text); notice.value = '已复制到剪贴板' }
  catch { editorError.value = '剪贴板不可用，请选中文本手动复制' }
}
async function renderTemplate() {
  if (!current.value || dirty.value) return
  const serial = ++renderSerial; renderBusy.value = true; editorError.value = ''
  try {
    const result = await request<{text: string}>(api(`/${current.value.id}/render`), json('POST', { version: current.value.version, values: values.value }))
    if (serial === renderSerial) rendered.value = result.text
  } catch (exc) { if (serial === renderSerial) editorError.value = message(exc) }
  finally { renderBusy.value = false }
}
async function copyContext() {
  if (!current.value) return
  busy.value = true
  try {
    const result = await request<{text: string; paths: string[]}>(api(`/${current.value.id}/context`), json('POST', {version: current.value.version, paths: clipboardPaths.value}))
    await copyText(result.text); notice.value = `已复制 ${result.paths.length} 个文本文件的完整上下文`
  } catch (exc) { editorError.value = message(exc) }
  finally { busy.value = false }
}
async function readText(record: Resource, path: string) {
  const response = await fetch(fileUrl(record, path))
  if (!response.ok) { const result = await response.json().catch(() => ({})); throw new Error(result.error || '文件读取失败') }
  if (!response.headers.get('content-type')?.startsWith('text/')) return null
  return await response.text()
}
async function openFile(path: string) {
  const serial = ++fileSerial; filePath.value = path; fileLoading.value = true; editorError.value = ''
  tab.value = 'files'
  try {
    const pending = changes.value[path]
    const text = path === 'SKILL.md' ? draft.value.body : pending?.text ?? (pending?.base64 ? null : current.value ? await readText(current.value, path) : null)
    if (serial !== fileSerial) return
    fileBinary.value = text === null; fileText.value = text || ''
  } catch (exc) { if (serial === fileSerial) editorError.value = message(exc) }
  finally { if (serial === fileSerial) fileLoading.value = false }
}
function stageText(text: string) {
  changes.value = { ...changes.value, [filePath.value]: {path: filePath.value, text} }
  if (filePath.value === 'SKILL.md') draft.value.body = text
}
function removeFile(path: string) {
  changes.value = { ...changes.value, [path]: { path, remove: true } }
  draft.value.context_paths = draft.value.context_paths.filter(p => p !== path)
  if (filePath.value === path) void openFile('SKILL.md')
}
async function addAttachments(event: Event) {
  const input = event.target as HTMLInputElement
  try {
    for (const file of [...(input.files || [])]) {
      if (file.size > 20 * 1024 ** 2) throw new Error('单个附件不得超过 20 MiB')
      const path = [uploadPrefix.value.trim().replace(/\/$/, ''), file.name].filter(Boolean).join('/')
      const base64 = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file) })
      changes.value = { ...changes.value, [path]: { path, base64 } }
    }
    notice.value = '附件已加入草稿，保存后生效'
  } catch (exc) { editorError.value = message(exc) }
  finally { input.value = '' }
}
async function importResource(event: Event) {
  const input = event.target as HTMLInputElement, files = [...(input.files || [])]
  if (!files.length) return
  const action = async () => {
    busy.value = true; error.value = ''
    try {
      const form = new FormData()
      files.forEach(file => form.append('files', file, file.webkitRelativePath || file.name))
      const record = await request<Resource>(api('/import'), { method: 'POST', body: form })
      setRecord(record); tab.value = 'overview'; notice.value = '已导入为自定义资源'
      await Promise.all([loadList(), loadTags()])
    } catch (exc) { error.value = message(exc) }
    finally { busy.value = false }
  }
  requestLeave(action); input.value = ''
}
async function showHistory() {
  tab.value = 'history'; if (!current.value) return
  const serial = ++historySerial, id = current.value.id; historyBusy.value = true
  try { const result = await request<{items: ResourceVersion[]}>(api(`/${id}/versions`)); if (serial === historySerial) versions.value = result.items }
  catch (exc) { if (serial === historySerial) editorError.value = message(exc) }
  finally { if (serial === historySerial) historyBusy.value = false }
}
async function selectVersion(number: number) {
  if (!current.value) return
  const serial = ++historySerial, id = current.value.id; historyBusy.value = true; history.value = null; ++historyFileSerial
  try { const result = await request<Resource>(api(`/${id}/versions/${number}`)); if (serial === historySerial) { history.value = result; historyText.value = ''; historyPath.value = '' } }
  catch (exc) { if (serial === historySerial) editorError.value = message(exc) }
  finally { if (serial === historySerial) historyBusy.value = false }
}
async function historicalFile(file: ResourceFile) {
  if (!history.value) return
  const serial = ++historyFileSerial, record = history.value
  try { const text = await readText(record, file.path); if (serial === historyFileSerial) { historyPath.value = file.path; historyText.value = text ?? '此附件为二进制文件，请下载该版本 ZIP。' } }
  catch (exc) { if (serial === historyFileSerial) editorError.value = message(exc) }
}
async function restore() {
  if (!current.value || !history.value) return
  busy.value = true
  try {
    const record = await request<Resource>(api(`/${current.value.id}/restore`), json('POST', {version: current.value.version, number: history.value.number}))
    setRecord(record); tab.value = 'overview'; notice.value = `已恢复为新版本 v${record.number}`
    await Promise.all([loadList(), loadTags()])
  } catch (exc) { editorError.value = message(exc) }
  finally { busy.value = false }
}
function detectVariables() {
  const existing = new Map(draft.value.variables.map(v => [v.name, v]))
  for (const match of draft.value.body.matchAll(/\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g)) {
    if (!existing.has(match[1])) existing.set(match[1], {name: match[1], label: match[1], type: 'textarea', description: '', required: true, default: null, options: [], min: null, max: null})
  }
  draft.value.variables = [...existing.values()]
}
function unload(event: BeforeUnloadEvent) { if (active.value && (dirty.value || busy.value)) { event.preventDefault(); event.returnValue = '' } }
function keyboard(event: KeyboardEvent) {
  if (active.value && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault(); if (!guardOpen.value && !deleteOpen.value) void save()
  }
}
onMounted(() => { void loadList(); void loadTags(); window.addEventListener('beforeunload', unload); window.addEventListener('keydown', keyboard) })
onActivated(() => { active.value = true })
onDeactivated(() => { active.value = false; clearHover() })
onUnmounted(() => { controller?.abort(); clearHover(); window.removeEventListener('beforeunload', unload); window.removeEventListener('keydown', keyboard) })
</script>

<template>
  <div class="resource-shell" :class="{ 'resource-focus': focused, 'resource-open': mobileOpen }">
    <header class="app-header resource-header"><a class="brand" href="/" @click.prevent="emit('navigate', 'prompts')"><span class="brand-mark" aria-hidden="true">a<span>i</span></span><span><strong>AI 资源库</strong><small>ai-library</small></span></a>
      <form class="global-search" role="search" @submit.prevent="query = search.trim()"><span aria-hidden="true">⌕</span><input v-model="search" :aria-label="`搜索${title}`" placeholder="搜索名称、简介或正文…"><button>搜索 ↵</button></form>
      <div class="header-actions"><button class="mobile-filter-button" @click="mobileFilters = !mobileFilters">筛选</button><button class="icon-button" aria-label="切换主题" @click="emit('theme', theme === 'dark' ? 'light' : 'dark')">☼</button><button :disabled="busy || loading" @click="loadList(); loadTags()">刷新</button><button :disabled="busy" @click="importInput?.click()">导入</button><button class="primary" :disabled="busy" @click="newRecord">＋ 新建{{ kind === 'skills' ? ' Skill' : '模板' }}</button></div>
      <input ref="importInput" hidden type="file" :accept="kind === 'skills' ? '.zip,.md' : '.zip,.md'" @change="importResource"><input ref="directoryInput" hidden type="file" multiple webkitdirectory @change="importResource">
    </header>
    <aside class="sidebar resource-nav" :class="{ 'mobile-open': mobileFilters }" aria-label="资源导航与筛选"><div class="section-label">资源库 <span>03</span></div>
      <button class="nav-item" @click="emit('navigate', 'prompts')">▤　提示词</button><button class="nav-item" :class="{ active: kind === 'templates' }" @click="emit('navigate', 'templates')">▧　提示词模板</button><button class="nav-item" :class="{ active: kind === 'skills' }" @click="emit('navigate', 'skills')">◇　Skills</button>
      <div class="sidebar-section"><div class="section-label">来源 <button class="text-button" @click="resetFilters">重置</button></div><div class="segmented wrap"><button v-for="option in [{id:'all',label:'全部'},{id:'builtin',label:'内置'},{id:'custom',label:'自定义'}]" :key="option.id" :aria-pressed="origin === option.id" @click="origin = option.id">{{ option.label }}</button></div>
        <label class="filter-select">适用模型<select v-model="model" aria-label="适用模型筛选"><option value="">全部</option><option v-for="item in models" :key="item">{{ item }}</option></select></label>
        <label v-if="kind === 'templates'" class="filter-select">模板类型<select v-model="templateKind" aria-label="模板类型筛选"><option value="">全部</option><option value="assistant">助手指令</option><option value="prompt">成品提示词</option></select></label>
        <button class="nav-item" :class="{ chosen: untagged }" @click="untagged = !untagged"><span>无标签</span><span class="count">{{ untaggedCount }}</span></button>
      </div>
      <div class="sidebar-section"><div class="section-label">标签 <span>{{ tags.length }}</span></div><input v-model="tagSearch" class="resource-tag-search" aria-label="搜索资源标签" placeholder="搜索标签…"><div class="tag-list"><button v-for="tag in tags.filter(t => t.name.includes(tagSearch.toLowerCase()))" :key="tag.id" class="tag-nav" :aria-pressed="filterTags.includes(tag.name)" @click="toggleTag(tag.name)"><span># {{ tag.name }}</span><span class="count">{{ tag.count }}</span></button></div><p v-if="!tags.length" class="muted small">尚无标签</p></div>
      <footer class="sidebar-footer">{{ kind === 'skills' ? '文件 · 参考资料 · 完整版本' : '填参数 · 预览 · 复制' }}<small>数据保存在本地 SQLite</small></footer>
    </aside>
    <button v-if="mobileFilters" class="filter-backdrop" aria-label="收起筛选" @click="mobileFilters = false"></button>
    <main class="resource-browser" :aria-label="`${title}浏览`"><div class="eyebrow">{{ kind === 'skills' ? 'SKILL LIBRARY' : 'TEMPLATE LIBRARY' }}</div><h1>{{ title }} <span>{{ total }}</span></h1><p class="browse-description">{{ kind === 'skills' ? '把方法、参考与经验，整理成可复用的技能。' : '留下好用的指令，让下一次创作更从容。' }}</p>
      <div v-if="filterTags.length" class="active-filters"><button v-for="tag in filterTags" :key="tag" class="chip selected" @click="toggleTag(tag)">{{ tag }} ×</button></div>
      <div v-if="error" class="banner error" role="alert">{{ error }}</div><div class="results-scroll" :aria-busy="loading"><p v-if="loading" class="loading-line">正在查询…</p><div v-if="!items.length && !loading" class="empty-state"><h2>没有匹配资源</h2><p>调整筛选条件，或导入自己的内容。</p></div>
        <div class="resource-cards" :class="{stale: loading}"><article v-for="item in items" :key="item.id" class="prompt-card resource-card" :class="{ active: current?.id === item.id }" @mouseenter="showHover(item.id)" @mouseleave="clearHover"><button class="resource-card-button" :disabled="loading" @focus="showHover(item.id)" @blur="clearHover" :aria-label="`打开${item.title}`" @click="clearHover(); requestLeave(() => openRecord(item.id))"><div class="card-top"><span class="resource-origin">{{ item.builtin ? '内置' : '自定义' }}<template v-if="item.source?.author === 'MiniMax-AI'"> · {{ item.builtin ? 'MiniMax 官方' : '源自 MiniMax' }}</template></span><span class="muted small">v{{ item.number }}</span></div><h2>{{ item.title }}</h2><p>{{ item.description || item.excerpt }}</p><div class="card-tags"><span v-for="tag in item.tags.slice(0,5)" :key="tag" class="mini-tag">{{ tag }}</span></div><footer class="card-meta"><span>{{ item.target_models.join(' · ') || '通用' }}</span><span>{{ kind === 'skills' ? `${item.file_count} 文件` : item.template_kind === 'assistant' ? '助手指令' : '成品提示词' }}</span></footer></button></article></div>
      </div><aside v-if="hover" class="resource-hover" aria-label="资源只读预览"><div class="section-label">快速预览 · 只读</div><h3>{{ hover.title }}</h3><p>{{ hover.body.slice(0, 900) }}</p></aside><footer class="pagination"><button :disabled="page <= 1 || loading" @click="page--">上一页</button><span>{{ page }} / {{ pages }}</span><button :disabled="page >= pages || loading" @click="page++">下一页</button></footer>
    </main>
    <section class="resource-workspace" aria-label="资源工作区"><header class="resource-workspace-head"><button class="mobile-back text-button" @click="mobileOpen = false">← 返回列表</button><div v-if="current || creating" class="resource-document-heading"><div><span class="eyebrow">{{ current?.builtin ? 'BUILT-IN PRESET' : 'YOUR RESOURCE' }}</span><h2>{{ draft.title || `新建${title}` }}</h2></div><button :aria-pressed="focused" @click="focused = !focused">{{ focused ? '退出聚焦' : '聚焦阅读' }}</button></div>
        <div v-if="current || creating" class="resource-tabs"><button v-if="current" :aria-pressed="tab === 'overview'" @click="tab = 'overview'">概览</button><button v-if="kind === 'templates' && current" :aria-pressed="tab === 'use'" @click="tab = 'use'">使用</button><button :aria-pressed="tab === 'edit'" @click="tab = 'edit'">{{ readonly ? '查看定义' : '编辑' }}</button><button v-if="kind === 'skills'" :aria-pressed="tab === 'files'" @click="openFile(filePath)">文件</button><button v-if="current" :aria-pressed="tab === 'history'" @click="showHistory">历史版本</button><span class="save-state" :class="{ modified: dirty }">{{ busy ? '处理中…' : dirty ? '● 未保存' : current ? `v${current.number} · 已同步` : '新资源' }}</span></div>
      </header>
      <div v-if="editorError" class="banner error resource-error" role="alert">{{ editorError }}<button v-if="current" :disabled="busy" @click="requestLeave(() => openRecord(current!.id))">重新加载</button></div>
      <div v-if="!current && !creating" class="empty-state resource-welcome"><div class="empty-symbol">{{ kind === 'skills' ? '◇' : '▧' }}</div><h2>{{ kind === 'skills' ? '方法，也可以成为资产' : '从一份好模板开始' }}</h2><p>选择左侧资源阅读与使用。<br>内置预设可复制为自己的版本。</p><button v-if="kind === 'skills'" @click="directoryInput?.click()">导入 Skill 目录</button></div>
      <div v-else class="resource-content" :class="{ 'file-layout': tab === 'files' }">
        <template v-if="tab === 'overview' && current"><div class="resource-overview"><div class="resource-badges"><span class="chip">{{ current.builtin ? '内置只读' : '自定义' }}</span><span v-for="tag in current.tags" :key="tag" class="chip">{{ tag }}</span></div><p class="resource-description">{{ current.description }}</p><p v-if="current.compatibility" class="compatibility"><strong>运行兼容性</strong>{{ current.compatibility }}</p><div v-if="current.source?.author || current.derived_from" class="resource-provenance"><span>来源：{{ current.source.author || '自定义' }}</span><a v-for="url in current.source.reference_urls || []" :key="url" :href="sourceUrl(url)" target="_blank" rel="noopener noreferrer">参考规范 ↗</a><a v-if="sourceUrl(current.source.url)" :href="sourceUrl(current.source.url)" target="_blank" rel="noopener noreferrer">查看原始来源 ↗</a><span v-if="current.source.commit">固定提交 {{ current.source.commit.slice(0,12) }}</span><span v-if="current.derived_from">源自资源 #{{ current.derived_from.id }} · v{{ current.derived_from.number }}</span></div>
          <div v-if="kind === 'skills' && current.files.some(f => f.path === 'SKILL.cn.md')" class="resource-action-row"><button @click="openFile('SKILL.cn.md')">阅读中文入口</button></div><SafeMarkdown :text="current.body" :kind="kind" :resource-id="current.id" :number="current.number" :files="current.files" :current-path="kind === 'skills' ? 'SKILL.md' : 'template.md'" @file="openFile" />
          <div v-if="kind === 'skills'" class="context-copy"><h3>复制文本上下文</h3><p class="muted small">默认包含入口及文本参考资料。附件与完整目录请导出 ZIP；复制不会执行脚本。</p><label v-for="file in current.files" :key="file.path" class="check-label"><input v-model="clipboardPaths" type="checkbox" :value="file.path">{{ file.path }}</label><button :disabled="busy || !clipboardPaths.length" @click="copyContext">复制完整文本上下文</button></div>
        </div></template>
        <div v-else-if="tab === 'use' && current" class="template-use"><div class="banner">{{ current.template_kind === 'assistant' ? '填写后得到发给外部 AI 的助手指令；本页不调用模型。' : '填写后直接组合成品提示词；不会自动扩写或理解素材。' }}</div><p v-if="dirty" class="warning">请先保存模板修改，再使用已保存版本。</p><form @submit.prevent="renderTemplate"><label v-for="variable in current.variables" :key="variable.name" class="stack-label">{{ variable.label }}{{ variable.required ? ' *' : '' }}<small v-if="variable.description">{{ variable.description }}</small><textarea v-if="variable.type === 'textarea'" v-model="values[variable.name]" :aria-label="variable.label" rows="4" :required="variable.required"></textarea><select v-else-if="variable.type === 'select'" v-model="values[variable.name]" :aria-label="variable.label" :required="variable.required"><option value="" disabled>请选择</option><option v-for="option in variable.options" :key="option">{{ option }}</option></select><input v-else v-model="values[variable.name]" :type="variable.type === 'number' ? 'number' : 'text'" :min="variable.min ?? undefined" :max="variable.max ?? undefined" step="any" :aria-label="variable.label" :required="variable.required"></label><button class="primary" :disabled="dirty || renderBusy">{{ renderBusy ? '正在渲染…' : '渲染预览' }}</button></form>
          <div v-if="rendered" class="rendered-output"><div class="section-label">{{ current.template_kind === 'assistant' ? '完整助手指令' : '成品提示词' }}<span>{{ rendered.length.toLocaleString() }} 字符</span></div><textarea :value="rendered" readonly aria-label="渲染结果"></textarea><div class="resource-action-row"><button @click="copyText(rendered)">复制渲染结果</button><button v-if="current.template_kind === 'prompt'" @click="requestLeave(() => emit('prompt', {text: rendered, media_type: current!.target_models.some(m => m.includes('H3')) ? 'video' : 'image'}))">存为提示词</button></div></div>
        </div>
        <form v-else-if="tab === 'edit'" class="resource-edit" @submit.prevent="save"><div v-if="readonly" class="banner">内置预设由仓库维护。点击“复制并编辑”创建独立副本，后续更新不会覆盖你的内容。</div><fieldset :disabled="readonly || busy"><label class="stack-label">名称<input v-model="draft.title" aria-label="资源名称" required maxlength="200"></label><label class="stack-label">简介<textarea v-model="draft.description" aria-label="资源简介" rows="3"></textarea></label><div class="metadata-grid"><label>适用模型（逗号分隔）<input v-model="modelText" aria-label="资源适用模型"></label><label>语言<select v-model="draft.language"><option value="zh">中文</option><option value="en">英文</option><option value="mixed">中英文</option></select></label><label v-if="kind === 'templates'">模板类型<select v-model="draft.template_kind" aria-label="编辑模板类型"><option value="assistant">助手指令</option><option value="prompt">成品提示词</option></select></label></div><div class="editor-group"><div class="section-label">标签</div><TagEditor ref="tagEditor" v-model="draft.tags" :suggestions="tags" label="添加资源标签" :disabled="readonly || busy" @pending="pendingTag = $event" /></div><label class="stack-label editor-group">{{ kind === 'skills' ? 'SKILL.md 原文（含 frontmatter）' : '模板正文' }}<textarea v-model="draft.body" aria-label="资源正文" class="resource-body-editor" spellcheck="false"></textarea></label><template v-if="kind === 'templates'"><div class="resource-action-row"><button type="button" @click="detectVariables">从正文提取变量</button></div><VariableEditor v-model="draft.variables" :disabled="readonly || busy" /><div v-if="localFiles.length" class="editor-group"><div class="section-label">渲染时附加的参考文件</div><label v-for="file in localFiles" :key="file.path" class="check-label"><input v-model="draft.context_paths" type="checkbox" :value="file.path">{{ file.path }}</label></div></template></fieldset></form>
        <template v-else-if="tab === 'files'"><aside class="resource-file-tree" aria-label="Skill 文件树"><div class="section-label">文件 <span>{{ localFiles.length }}</span></div><button v-for="file in localFiles" :key="file.path" :title="file.path" :aria-pressed="filePath === file.path" :style="{paddingLeft: `${8 + Math.min(file.path.split('/').length - 1, 4) * 10}px`}" @click="openFile(file.path)"><span>{{ file.path }}</span><small v-if="changes[file.path]">●</small></button><div v-if="!readonly" class="attachment-controls"><label>新增附件目录<input v-model="uploadPrefix" aria-label="附件目录" placeholder="assets"></label><button :disabled="busy" @click="attachmentInput?.click()">添加附件</button><input ref="attachmentInput" hidden type="file" multiple @change="addAttachments"></div></aside><div class="resource-file-view"><div class="resource-file-heading"><code>{{ filePath }}</code><div><button v-if="!fileBinary" class="text-button" @click="copyText(fileText)">复制文件文本</button><a v-if="current && !changes[filePath]" :href="fileUrl(current, filePath)" :download="filePath.split('/').at(-1)">下载</a><button v-if="!readonly && filePath !== 'SKILL.md'" class="text-button danger" @click="removeFile(filePath)">移除文件</button></div></div><p v-if="fileLoading" class="muted">正在读取…</p><template v-else><img v-if="fileBinary && isImage(selectedFile)" :src="imageUrl" class="resource-image" :alt="filePath"><p v-else-if="fileBinary" class="muted">{{ changes[filePath]?.base64 ? '该附件尚未保存，保存后可预览或下载。' : '此附件为二进制内容，可下载或导出完整 ZIP。' }}</p><template v-else><textarea v-if="!readonly" v-model="fileText" aria-label="文件内容编辑" class="file-text-editor" spellcheck="false" :disabled="busy" @input="stageText(fileText)"></textarea><SafeMarkdown v-if="/\.(md|txt)$/.test(filePath)" :text="fileText" :kind="kind" :resource-id="current?.id" :number="current?.number" :files="localFiles" :current-path="filePath" @file="openFile" /><pre v-else-if="readonly" class="file-code">{{ fileText }}</pre></template></template></div></template>
        <div v-else-if="tab === 'history'" class="resource-history"><div class="version-list"><button v-for="version in versions" :key="version.number" :disabled="busy || historyBusy" :aria-pressed="history?.number === version.number" @click="selectVersion(version.number)"><strong>v{{ version.number }}</strong><span>{{ date(version.created_at) }}</span><small v-if="version.number === current?.number">当前</small></button></div><div v-if="history" class="history-detail"><div class="resource-action-row"><span>查看 v{{ history.number }}</span><a :href="`/api/v1/${kind}/${history.id}/export?number=${history.number}`">导出该版本</a><button v-if="readonly" :disabled="busy" @click="copyResource(history.number)">从此版本复制</button><button v-else :disabled="busy" @click="requestLeave(restore)">恢复为新版本</button></div><h3>{{ history.title }}</h3><p>{{ history.description }}</p><div class="chips"><span v-for="tag in history.tags" :key="tag" class="chip">{{ tag }}</span></div><SafeMarkdown :text="history.body" :kind="kind" :resource-id="history.id" :number="history.number" :files="history.files" current-path="SKILL.md" @file="path => historicalFile(history!.files.find(f => f.path === path)!)" /><details v-if="history.variables.length"><summary>该版本的变量定义</summary><pre>{{ JSON.stringify(history.variables, null, 2) }}</pre></details><div v-if="history.files.length" class="historical-files"><h3>该版本文件</h3><button v-for="file in history.files" :key="file.path" @click="historicalFile(file)">{{ file.path }}</button><pre v-if="historyPath" class="file-code">{{ historyPath }}{{ '\n\n' }}{{ historyText }}</pre></div></div><p v-else class="muted">选择一个版本查看完整内容。</p></div>
      </div>
      <footer v-if="current || creating" class="resource-footer"><div class="resource-action-row"><button v-if="readonly" class="primary" :disabled="busy" @click="copyResource()">复制并编辑</button><template v-else><button class="primary" :disabled="busy || (!dirty && !creating)" @click="save">{{ busy ? '处理中…' : '保存修改' }} <kbd>⌘/Ctrl S</kbd></button><button :disabled="busy || !dirty" @click="requestLeave(() => { if(current) setRecord(current); else setRecord(null) })">撤销修改</button></template><a v-if="current" class="button-link" :href="`/api/v1/${kind}/${current.id}/export`">导出 ZIP</a><button v-if="current" :disabled="busy" @click="copyText(current!.body)">{{ kind === 'skills' ? '复制入口' : '复制原文' }}</button><button v-if="current && !readonly" class="text-button danger" :disabled="busy" @click="requestLeave(() => { deleteOpen = true })">删除</button></div></footer>
    </section>
    <div v-if="notice" class="toast" role="status">{{ notice }}<button aria-label="关闭通知" @click="notice = ''">×</button></div>
    <ModalShell v-if="guardOpen" title="有尚未保存的资源修改" :busy="busy" @close="guardOpen = false; guarded = null"><p>保存草稿，或放弃后继续。</p><p v-if="editorError" class="error-text" role="alert">{{ editorError }}</p><div class="modal-actions"><button :disabled="busy" @click="guardOpen = false; guarded = null">取消</button><button :disabled="busy" @click="finishGuard(false)">放弃修改</button><button class="primary" :disabled="busy" @click="finishGuard(true)">保存并继续</button></div></ModalShell>
    <ModalShell v-if="deleteOpen" title="删除自定义资源" :busy="busy" @close="deleteOpen = false"><p>将删除「{{ current?.title }}」及其全部历史版本。共享附件只有在不再被其他资源引用时才移除。</p><p v-if="editorError" class="error-text">{{ editorError }}</p><div class="modal-actions"><button :disabled="busy" @click="deleteOpen = false">取消</button><button class="destructive" :disabled="busy" @click="removeRecord">确认删除</button></div></ModalShell>
  </div>
</template>
