<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ApiError, json, request } from './api'
import ModalShell from './components/ModalShell.vue'
import TagEditor from './components/TagEditor.vue'
import ResourceLibrary from './resources/ResourceLibrary.vue'
import type { ResourceKind } from './resources/types'
import { emptyFilters, emptyPrompt, fieldOptions, inputOf, labels } from './resources/prompts/types'
import type { BatchAction, BatchPreview, BatchRequest, Filters, Page, Prompt, PromptInput, Summary, Tag, TagResult } from './resources/prompts/types'

const activeResource = ref<'prompts' | ResourceKind>('prompts')
const resourcePage = ref<InstanceType<typeof ResourceLibrary>>()
function changeResource(kind: 'prompts' | ResourceKind) {
  if (kind === activeResource.value) return
  const navigate = () => { activeResource.value = kind }
  if (activeResource.value === 'prompts') guard(navigate)
  else resourcePage.value?.requestLeave(navigate)
}
async function fromTemplate(value: { text: string, media_type: string }) {
  activeResource.value = 'prompts'
  newRecord()
  await nextTick()
  draft.value.prompt = value.text
  draft.value.media_type = value.media_type
}

const filters = reactive(emptyFilters())
const searchText = ref('')
const items = ref<Summary[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const loading = ref(false)
const error = ref('')
const notice = ref('')
const connected = ref(false)
const tags = ref<Tag[]>([])
const tagSearch = ref('')
const tagPage = ref(1)
const tagLoading = ref(false)
const tagTotal = ref(0)
const untagged = ref(0)
const suggestions = ref<Tag[]>([])
const view = ref(localStorage.getItem('ai-library-view') || 'cards')
const theme = ref(localStorage.getItem('ai-library-theme') || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'))
const selection = ref(new Set<number>())
const selectAll = ref(false)
const selectedCount = computed(() => selectAll.value ? total.value : selection.value.size)
const allPageSelected = computed(() => items.value.length > 0 && items.value.every(item => selection.value.has(item.id)))

const mode = ref<'empty' | 'new' | 'record'>('empty')
const current = ref<Prompt | null>(null)
const draft = ref<PromptInput>(emptyPrompt())
const baseline = ref(JSON.stringify(draft.value))
const tagPending = ref(false)
const dirty = computed(() => mode.value !== 'empty' && (tagPending.value || JSON.stringify(draft.value) !== baseline.value))
const saving = ref(false)
const detailLoading = ref(false)
const editorError = ref('')
const savedAt = ref('')
const detailTab = ref<'edit' | 'preview'>('edit')
const mobileDetail = ref(false)
const mobileFilters = ref(false)
const editorTags = ref<InstanceType<typeof TagEditor>>()
const hover = ref<Prompt | null>(null)
let hoverTimer: ReturnType<typeof setTimeout> | undefined
let hoverSerial = 0
let detailSerial = 0
let listSerial = 0
let listController: AbortController | undefined
let tagsSerial = 0
let suggestionSerial = 0
let tagTimer: ReturnType<typeof setTimeout> | undefined
let suggestionTimer: ReturnType<typeof setTimeout> | undefined
let lastFocus = 0

const guardOpen = ref(false)
let guardedAction: (() => void | Promise<void>) | null = null
const batchOpen = ref(false)
const batchAction = ref<BatchAction>('append')
const batchTags = ref<string[]>([])
const batchTagPending = ref(false)
const batchTagsInput = ref<InstanceType<typeof TagEditor>>()
const batchPreview = ref<BatchPreview | null>(null)
const batchBusy = ref(false)
const batchError = ref('')
let batchRequest: BatchRequest | null = null
let batchIds: number[] | null = null
let batchSession = 0
const actionLabel: Record<BatchAction, string> = { append: '追加标签', remove: '移除标签', replace: '替换全部标签', delete: '删除提示词' }

function message(exc: unknown) { return exc instanceof Error ? exc.message : String(exc) }
function queryString(value: Filters): URLSearchParams {
  const params = new URLSearchParams()
  for (const [key, val] of Object.entries(value)) {
    if (Array.isArray(val)) val.forEach(tag => params.append(key, tag))
    else params.set(key, String(val))
  }
  return params
}
async function loadList() {
  const serial = ++listSerial
  listController?.abort()
  listController = new AbortController()
  loading.value = true
  const params = queryString(filters)
  params.set('page', String(page.value)); params.set('page_size', String(pageSize.value))
  try {
    const result = await request<Page>(`/prompts?${params}`, { signal: listController.signal })
    if (serial !== listSerial) return
    connected.value = true
    items.value = result.items
    total.value = result.total
    error.value = ''
    if (page.value > pages.value) page.value = pages.value
  } catch (exc) {
    if (serial === listSerial && !(exc instanceof DOMException && exc.name === 'AbortError')) {
      error.value = message(exc); connected.value = false
      items.value = []; total.value = 0; clearSelection()
    }
  } finally { if (serial === listSerial) loading.value = false }
}
async function loadTags(append = false) {
  const serial = ++tagsSerial
  tagLoading.value = true
  try {
    const result = await request<TagResult>(`/tags?q=${encodeURIComponent(tagSearch.value)}&page=${tagPage.value}`)
    if (serial !== tagsSerial) return
    tags.value = append ? [...tags.value, ...result.items] : result.items
    tagTotal.value = result.total; untagged.value = result.untagged
  } catch (exc) { if (serial === tagsSerial) error.value = message(exc) }
  finally { if (serial === tagsSerial) tagLoading.value = false }
}
function searchTags() {
  clearTimeout(tagTimer)
  ++tagsSerial
  tagTimer = setTimeout(() => { tagPage.value = 1; void loadTags() }, 250)
}
function suggestTags(text: string) {
  clearTimeout(suggestionTimer)
  const serial = ++suggestionSerial
  suggestionTimer = setTimeout(async () => {
    try {
      const result = await request<TagResult>(`/tags?q=${encodeURIComponent(text)}`)
      if (serial === suggestionSerial) suggestions.value = result.items
    } catch { /* A failed suggestion never discards the typed tag. */ }
  }, 200)
}
async function refresh() {
  await Promise.all([loadList(), loadTags()])
  if (!dirty.value && mode.value === 'record' && current.value && !saving.value) {
    await openRecord(current.value.id)
  }
}
function clearSelection() { selection.value = new Set(); selectAll.value = false }
function jumpPage(event: Event) {
  const input = event.target as HTMLInputElement
  const value = Number(input.value)
  if (Number.isFinite(value)) page.value = Math.max(1, Math.min(pages.value, Math.floor(value)))
  input.value = String(page.value)
}
watch(filters, () => { clearSelection(); page.value = 1; void loadList() }, { deep: true })
watch(page, () => { void loadList() })
watch(pageSize, () => { page.value = 1; clearSelection(); void loadList() })
watch(view, value => localStorage.setItem('ai-library-view', value))
watch(theme, value => { document.documentElement.dataset.theme = value; localStorage.setItem('ai-library-theme', value) }, { immediate: true })
function resetFilters() { searchText.value = ''; Object.assign(filters, emptyFilters()) }
function toggleFilterTag(tag: string) {
  filters.tags = filters.tags.includes(tag) ? filters.tags.filter(t => t !== tag) : [...filters.tags, tag]
}
function toggleOne(id: number) {
  if (selectAll.value) { selectAll.value = false; selection.value = new Set(items.value.map(p => p.id)) }
  const next = new Set(selection.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  selection.value = next
}
function togglePage() {
  if (selectAll.value) { clearSelection(); return }
  const next = new Set(selection.value)
  if (allPageSelected.value) items.value.forEach(p => next.delete(p.id)); else items.value.forEach(p => next.add(p.id))
  selection.value = next
}
function guard(action: () => void | Promise<void>) {
  if (saving.value || batchBusy.value) return
  if (dirty.value) { guardedAction = action; guardOpen.value = true }
  else void action()
}
async function finishGuard(saveFirst: boolean) {
  if (saveFirst && !await save()) return
  if (!saveFirst) { draft.value = JSON.parse(baseline.value); tagPending.value = false }
  const action = guardedAction
  guardedAction = null; guardOpen.value = false
  await action?.()
}
async function openRecord(id: number) {
  const serial = ++detailSerial
  detailLoading.value = true
  editorError.value = ''
  try {
    const value = await request<Prompt>(`/prompts/${id}`)
    if (serial !== detailSerial) return
    current.value = value; draft.value = inputOf(value); baseline.value = JSON.stringify(draft.value)
    tagPending.value = false
    mode.value = 'record'; mobileDetail.value = true; savedAt.value = ''; detailTab.value = 'edit'
  } catch (exc) { if (serial === detailSerial) editorError.value = message(exc) }
  finally { if (serial === detailSerial) detailLoading.value = false }
}
function newRecord() {
  guard(() => {
    ++detailSerial; detailLoading.value = false
    current.value = null; draft.value = emptyPrompt(); baseline.value = JSON.stringify(draft.value)
    tagPending.value = false
    mode.value = 'new'; editorError.value = ''; mobileDetail.value = true; detailTab.value = 'edit'
  })
}
async function save(): Promise<boolean> {
  if (saving.value || mode.value === 'empty') return false
  editorTags.value?.flush()
  await nextTick()
  if (!draft.value.prompt.trim()) { editorError.value = '请输入提示词正文'; return false }
  saving.value = true; editorError.value = ''
  try {
    const body = structuredClone(JSON.parse(JSON.stringify(draft.value)))
    const result = mode.value === 'new'
      ? await request<Prompt>('/prompts', json('POST', body))
      : await request<Prompt>(`/prompts/${current.value!.id}`, json('PATCH', { ...body, version: current.value!.version }))
    current.value = result; draft.value = inputOf(result); baseline.value = JSON.stringify(draft.value)
    mode.value = 'record'; savedAt.value = new Date().toLocaleTimeString('zh-CN')
    notice.value = `已保存 #${result.id}`
    await Promise.all([loadList(), loadTags()])
    return true
  } catch (exc) { editorError.value = message(exc); return false }
  finally { saving.value = false }
}
function undo() {
  guard(() => { draft.value = JSON.parse(baseline.value); editorError.value = '' })
}
function clearHover() { clearTimeout(hoverTimer); ++hoverSerial; hover.value = null }
function showHover(id: number) {
  clearTimeout(hoverTimer)
  const serial = ++hoverSerial
  hoverTimer = setTimeout(async () => {
    try {
      const result = await request<Prompt>(`/prompts/${id}`)
      if (serial === hoverSerial) hover.value = result
    } catch { /* Hover failures do not interrupt editing. */ }
  }, 250)
}
async function copyPrompt() {
  try { await navigator.clipboard.writeText(draft.value.prompt); notice.value = '已复制正文' }
  catch { editorError.value = '无法访问剪贴板，请选中正文手动复制' }
}
function openBatch(action: BatchAction, ids: number[] | null = null) {
  guard(() => {
    batchSession++; batchAction.value = action; batchTags.value = []; batchTagPending.value = false; batchIds = ids
    batchPreview.value = null; batchRequest = null; batchError.value = ''; batchOpen.value = true
  })
}
function closeBatch() { if (!batchBusy.value) { batchSession++; batchOpen.value = false } }
watch([batchAction, batchTags], () => { batchPreview.value = null; batchRequest = null }, { deep: true })
watch(batchTagPending, value => { if (value) { batchPreview.value = null; batchRequest = null } })
async function previewBatch() {
  batchTagsInput.value?.flush(); await nextTick()
  batchBusy.value = true; batchError.value = ''
  const session = batchSession
  const value: BatchRequest = {
    selection: { mode: !batchIds && selectAll.value ? 'filter' : 'ids',
      ids: batchIds || [...selection.value], filters: JSON.parse(JSON.stringify(filters)) },
    action: batchAction.value, tags: [...batchTags.value],
  }
  try {
    const result = await request<BatchPreview>('/prompts/batch/preview', json('POST', value))
    if (session === batchSession) { batchPreview.value = result; batchRequest = value }
  } catch (exc) { if (session === batchSession) batchError.value = message(exc) }
  finally { if (session === batchSession) batchBusy.value = false }
}
async function applyBatch() {
  if (!batchPreview.value || !batchRequest) return
  batchBusy.value = true; batchError.value = ''
  try {
    const result = await request<{ affected: number }>('/prompts/batch/apply', json('POST', { ...batchRequest, token: batchPreview.value.token }))
    notice.value = `${actionLabel[batchAction.value]}完成，共 ${result.affected.toLocaleString()} 条`
    batchOpen.value = false; batchSession++; clearSelection(); clearHover()
    const selectedId = current.value?.id
    await Promise.all([loadList(), loadTags()])
    if (selectedId && !dirty.value) {
      try {
        const record = await request<Prompt>(`/prompts/${selectedId}`)
        current.value = record; draft.value = inputOf(record); baseline.value = JSON.stringify(draft.value)
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 404) {
          mode.value = 'empty'; current.value = null; mobileDetail.value = false
        } else editorError.value = message(exc)
      }
    }
  } catch (exc) { batchError.value = message(exc); batchPreview.value = null; batchRequest = null }
  finally { batchBusy.value = false }
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || saving.value) { event.preventDefault(); event.returnValue = '' } }
function keydown(event: KeyboardEvent) {
  if (activeResource.value !== 'prompts') return
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault()
    if (!guardOpen.value && !batchOpen.value) void save()
  }
}
function focusRefresh() {
  if (activeResource.value !== 'prompts') return
  if (Date.now() - lastFocus < 3000 || saving.value || batchOpen.value || guardOpen.value) return
  lastFocus = Date.now(); void refresh()
}
onMounted(() => {
  void loadList(); void loadTags()
  window.addEventListener('beforeunload', beforeUnload); window.addEventListener('keydown', keydown); window.addEventListener('focus', focusRefresh)
})
onUnmounted(() => {
  listController?.abort(); clearHover(); clearTimeout(tagTimer); clearTimeout(suggestionTimer)
  window.removeEventListener('beforeunload', beforeUnload); window.removeEventListener('keydown', keydown); window.removeEventListener('focus', focusRefresh)
})
</script>

<template>
  <div v-show="activeResource === 'prompts'" class="app-shell" :class="{ 'show-detail': mobileDetail }">
    <header class="app-header">
      <a class="brand" href="/" @click.prevent="guard(() => { resetFilters(); mode = 'empty'; mobileDetail = false })">
        <span class="brand-mark" aria-hidden="true">a<span>i</span></span>
        <span><strong>AI 资源库</strong><small>ai-library</small></span>
      </a>
      <form class="global-search" role="search" @submit.prevent="filters.q = searchText.trim()">
        <span aria-hidden="true">⌕</span><input v-model="searchText" aria-label="搜索正文或 ID" placeholder="搜索提示词正文或 ID…"><button type="submit">搜索 <kbd>↵</kbd></button>
      </form>
      <div class="header-actions">
        <button class="mobile-filter-button" :aria-expanded="mobileFilters" @click="mobileFilters = !mobileFilters">{{ mobileFilters ? '关闭筛选' : '筛选' }}</button>
        <button class="icon-button" :aria-label="theme === 'dark' ? '切换浅色主题' : '切换深色主题'" @click="theme = theme === 'dark' ? 'light' : 'dark'">{{ theme === 'dark' ? '☼' : '◐' }}</button>
        <button :disabled="loading || saving" @click="refresh">刷新</button>
        <button class="primary" :disabled="saving" @click="newRecord">＋ 新建提示词</button>
      </div>
    </header>

    <aside class="sidebar" :class="{ 'mobile-open': mobileFilters }" aria-label="资源导航与筛选">
      <div class="section-label">资源库 <span>03</span></div>
      <button class="nav-item active" @click="resetFilters"><span>▤　提示词</span><span class="nav-dot"></span></button>
      <button class="nav-item" @click="changeResource('templates')"><span>▧　提示词模板</span></button>
      <button class="nav-item" @click="changeResource('skills')"><span>◇　Skills</span></button>
      <div class="sidebar-section">
        <div class="section-label">快捷浏览</div>
        <button class="nav-item" :class="{ chosen: !filters.untagged }" @click="filters.untagged = false"><span>全部提示词</span></button>
        <button class="nav-item" :class="{ chosen: filters.untagged }" @click="filters.untagged = true"><span>无标签</span><span class="count">{{ untagged.toLocaleString() }}</span></button>
      </div>
      <div class="sidebar-section">
        <div class="section-label">筛选条件 <button class="text-button" @click="resetFilters">重置</button></div>
        <div v-for="field in fieldOptions" :key="field.key" class="filter-group">
          <div class="field-label">{{ field.label }}</div>
          <div class="segmented wrap">
            <button v-for="option in ['全部', ...field.values]" :key="option" :aria-pressed="filters[field.key] === option" @click="filters[field.key] = option">{{ labels[option] || option }}</button>
          </div>
        </div>
        <label class="filter-select">安全性<select v-model="filters.safety"><option value="全部">全部</option><option value="safe">安全</option><option value="nsfw">NSFW</option></select></label>
        <label class="filter-select">动态性<select v-model="filters.dynamic"><option value="全部">全部</option><option value="dynamic">动态</option><option value="static">静态</option></select></label>
        <label class="check-label"><input v-model="filters.match_size" type="checkbox">匹配建议尺寸</label>
        <div v-if="filters.match_size" class="size-inputs"><label>宽<input v-model.number="filters.width" type="number" min="1" max="16384"></label><span>×</span><label>高<input v-model.number="filters.height" type="number" min="1" max="16384"></label></div>
      </div>
      <div class="sidebar-section tags-section">
        <div class="section-label">标签 <span>{{ tagTotal }}</span></div>
        <input v-model="tagSearch" aria-label="搜索标签" placeholder="搜索标签…" @input="searchTags">
        <div class="tag-list"><button v-for="tag in tags" :key="tag.id" class="tag-nav" :aria-pressed="filters.tags.includes(tag.name)" @click="toggleFilterTag(tag.name)"><span># {{ tag.name }}</span><span class="count">{{ tag.count.toLocaleString() }}</span></button></div>
        <p v-if="!tags.length" class="muted small">没有匹配标签</p>
        <button v-if="tags.length < tagTotal" class="text-button" :disabled="tagLoading" @click="tagPage++; loadTags(true)">加载更多标签</button>
      </div>
      <footer class="sidebar-footer"><span class="status-dot" :class="{ online: connected }"></span>{{ connected ? '服务已连接' : '服务未连接' }}<small>本地资源 · 独立管理</small></footer>
    </aside>
    <button v-if="mobileFilters" class="filter-backdrop" aria-label="收起筛选" @click="mobileFilters = false"></button>

    <main class="browser-pane" aria-label="提示词浏览">
      <div class="browse-heading"><div><div class="eyebrow">PROMPT LIBRARY</div><h1>{{ filters.untagged ? '无标签提示词' : '提示词' }} <span>{{ total.toLocaleString() }}</span></h1></div><div class="segmented" aria-label="浏览方式"><button :aria-pressed="view === 'cards'" @click="view = 'cards'">卡片</button><button :aria-pressed="view === 'list'" @click="view = 'list'">列表</button></div></div>
      <p class="browse-description">让每一个好想法，都有迹可循。</p>
      <div v-if="filters.tags.length || filters.q" class="active-filters"><span v-if="filters.q" class="chip">搜索：{{ filters.q }} <button aria-label="清除搜索" @click="filters.q = ''; searchText = ''">×</button></span><button v-for="tag in filters.tags" :key="tag" class="chip selected" @click="toggleFilterTag(tag)"># {{ tag }} ×</button></div>
      <div v-if="error" class="banner error" role="alert">{{ error }}<button @click="refresh">重试</button></div>
      <div class="selection-toolbar"><label class="check-label"><input type="checkbox" :checked="selectAll || allPageSelected" :disabled="!items.length || loading" @change="togglePage">当前页</label><span class="muted">{{ selectedCount ? `已选 ${selectedCount.toLocaleString()} 条` : '悬停预览，点击编辑' }}</span><button v-if="items.length && !selectAll" class="text-button" @click="selectAll = true; selection = new Set()">选择全部筛选结果</button><button v-if="selectedCount" class="text-button" @click="clearSelection">取消选择</button></div>
      <div v-if="selectedCount" class="batch-toolbar"><span>{{ selectAll ? '全部匹配结果' : '勾选记录' }}</span><button @click="openBatch('append')">追加标签</button><button @click="openBatch('remove')">移除标签</button><button @click="openBatch('replace')">替换标签</button><button class="danger" @click="openBatch('delete')">删除</button></div>
      <div class="results-scroll" :aria-busy="loading" @scroll="clearHover">
        <div v-if="loading" class="loading-line" role="status">正在查询…</div>
        <div v-if="!items.length && !loading" class="empty-state"><div class="empty-symbol">▤</div><h2>{{ error ? '暂时无法读取资源库' : '这里还没有提示词' }}</h2><p>{{ error ? '检查服务与数据库配置后重试。' : '调整筛选条件，或创建第一条提示词。' }}</p><button v-if="!error" @click="newRecord">新建提示词</button></div>
        <div class="prompt-grid" :class="{ compact: view === 'list', stale: loading }">
          <article v-for="item in items" :key="item.id" class="prompt-card" :class="{ active: mode === 'record' && current?.id === item.id, checked: selectAll || selection.has(item.id) }" @mouseenter="showHover(item.id)" @mouseleave="clearHover">
            <div class="card-top"><label class="card-check"><input type="checkbox" :aria-label="`选择提示词 ${item.id}`" :checked="selectAll || selection.has(item.id)" @change="toggleOne(item.id)"><span>#{{ item.id }}</span></label><span class="media-badge">{{ labels[item.media_type] }}</span></div>
            <button class="card-content" :aria-label="`编辑提示词 ${item.id}`" @focus="showHover(item.id)" @blur="clearHover" @click="clearHover(); guard(() => openRecord(item.id))"><p>{{ item.excerpt }}</p></button>
            <div class="card-tags"><span v-for="tag in item.tags.slice(0, 4)" :key="tag" class="mini-tag">{{ tag }}</span><span v-if="item.tags.length > 4" class="muted small">+{{ item.tags.length - 4 }}</span><span v-if="!item.tags.length" class="untagged-label">尚无标签</span></div>
            <footer class="card-meta"><span>{{ labels[item.language] }} · {{ labels[item.prompt_type] }}<template v-if="item.nsfw"> · NSFW</template></span><span>{{ item.width }} × {{ item.height }}</span></footer>
          </article>
        </div>
      </div>
      <aside v-if="hover" class="hover-preview" aria-label="只读快速预览"><div class="section-label">快速预览 <span>#{{ hover.id }} · 只读</span></div><p>{{ hover.prompt }}</p><div class="chips"><span v-for="tag in hover.tags" :key="tag" class="mini-tag">{{ tag }}</span></div></aside>
      <footer class="pagination"><label>每页<select v-model.number="pageSize" aria-label="每页条数"><option :value="50">50 条</option><option :value="100">100 条</option></select></label><span>第 <input :value="page" aria-label="跳转页码" type="number" min="1" :max="pages" :disabled="loading" @change="jumpPage"> / {{ pages }} 页</span><button :disabled="page <= 1 || loading" @click="page--">上一页</button><button :disabled="page >= pages || loading" @click="page++">下一页</button></footer>
    </main>

    <aside class="editor-pane" aria-label="提示词详情">
      <header class="editor-heading"><button class="mobile-back text-button" @click="mobileDetail = false">← 返回列表</button><div class="section-label">{{ mode === 'new' ? '创建提示词' : '提示词详情' }}<span v-if="current">#{{ current.id }}</span></div><div v-if="mode !== 'empty'" class="editor-tabs"><button :aria-pressed="detailTab === 'edit'" @click="detailTab = 'edit'">编辑</button><button :aria-pressed="detailTab === 'preview'" @click="detailTab = 'preview'">实时预览</button><span class="save-state" :class="{ modified: dirty }">{{ saving ? '保存中…' : dirty ? '● 未保存' : savedAt ? `已保存 ${savedAt}` : '已同步' }}</span></div></header>
      <div v-if="editorError" class="banner error" role="alert">{{ editorError }}<button v-if="current" :disabled="saving" @click="guard(() => openRecord(current!.id))">重新加载</button></div>
      <div v-if="detailLoading" class="detail-loading" role="status">正在加载详情…</div>
      <div v-if="mode === 'empty'" class="empty-state editor-empty"><div class="empty-symbol">✧</div><h2>从一个想法开始</h2><p>选择提示词查看完整内容，<br>整理标签，或继续打磨文字。</p><button @click="newRecord">＋ 新建提示词</button></div>
      <div v-else class="editor-scroll">
        <form v-if="detailTab === 'edit'" class="editor-form" @submit.prevent="save"><fieldset :disabled="saving || detailLoading">
          <label class="editor-label">提示词正文<span>{{ draft.prompt.length.toLocaleString() }} 字符</span><textarea v-model="draft.prompt" aria-label="提示词正文" placeholder="写下你的创意…" class="prompt-textarea" spellcheck="false"></textarea></label>
          <div class="editor-group"><div class="section-label">标签</div><TagEditor ref="editorTags" v-model="draft.tags" :suggestions="suggestions" label="添加记录标签" @search="suggestTags" @pending="tagPending = $event" /></div>
          <div class="editor-group"><div class="section-label">属性</div><div class="metadata-grid"><label v-for="field in fieldOptions" :key="field.key">{{ field.label }}<select v-model="draft[field.key]" :aria-label="`编辑${field.label}`"><option v-for="value in field.values" :key="value" :value="value">{{ labels[value] }}</option></select></label></div><div class="toggle-row"><label class="check-label"><input v-model="draft.nsfw" type="checkbox">NSFW</label><label class="check-label"><input v-model="draft.dynamic" type="checkbox">动态提示词</label></div><div class="metadata-grid"><label>建议宽度<input v-model.number="draft.width" type="number" min="1" max="16384" required></label><label>建议高度<input v-model.number="draft.height" type="number" min="1" max="16384" required></label></div></div>
          <details v-if="current" class="record-info"><summary>记录信息</summary><p>创建时间 {{ current.created_at }}</p><p>SHA-1 <code>{{ current.shasum }}</code></p></details>
        </fieldset></form>
        <div v-else class="live-preview"><div class="section-label">当前编辑效果 <span>{{ dirty ? '草稿' : '已保存内容' }}</span></div><p>{{ draft.prompt || '正文为空' }}</p><div class="chips"><span v-for="tag in draft.tags" :key="tag" class="chip">{{ tag }}</span></div><dl><dt>语言 / 媒体</dt><dd>{{ labels[draft.language] }} / {{ labels[draft.media_type] }}</dd><dt>类型 / 格式</dt><dd>{{ labels[draft.prompt_type] }} / {{ labels[draft.content_format] }}</dd><dt>建议尺寸</dt><dd>{{ draft.width }} × {{ draft.height }}</dd><dt>内容属性</dt><dd>{{ draft.nsfw ? 'NSFW' : '安全' }} · {{ draft.dynamic ? '动态' : '静态' }}</dd></dl></div>
      </div>
      <footer v-if="mode !== 'empty'" class="editor-actions"><div><button class="primary" :disabled="saving || detailLoading || (!dirty && mode !== 'new')" @click="save">{{ saving ? '保存中…' : '保存修改' }} <kbd>⌘/Ctrl S</kbd></button><button :disabled="saving || !dirty" @click="undo">撤销修改</button></div><div><button @click="copyPrompt">复制正文</button><button v-if="current" class="danger text-button" :disabled="saving" @click="openBatch('delete', [current!.id])">删除记录</button></div></footer>
    </aside>
    <div v-if="notice" class="toast" role="status">{{ notice }}<button aria-label="关闭通知" @click="notice = ''">×</button></div>

    <ModalShell v-if="guardOpen" title="有尚未保存的修改" :busy="saving" @close="guardOpen = false; guardedAction = null"><p>保存当前修改，或放弃后继续。</p><p v-if="editorError" class="error-text" role="alert">{{ editorError }}</p><div class="modal-actions"><button :disabled="saving" @click="guardOpen = false; guardedAction = null">取消</button><button :disabled="saving" @click="finishGuard(false)">放弃修改</button><button class="primary" :disabled="saving" @click="finishGuard(true)">保存并继续</button></div></ModalShell>
    <ModalShell v-if="batchOpen" :title="actionLabel[batchAction]" :busy="batchBusy" @close="closeBatch">
      <p class="muted">{{ batchIds ? `指定 ${batchIds.length} 条记录` : selectAll ? '作用于全部筛选结果' : `作用于勾选的 ${selection.size} 条记录` }}</p>
      <TagEditor v-if="batchAction !== 'delete'" ref="batchTagsInput" v-model="batchTags" :suggestions="suggestions" :disabled="batchBusy" label="批量操作标签" @search="suggestTags" @pending="batchTagPending = $event" />
      <p v-if="batchAction === 'replace'" class="warning">{{ batchTags.length ? '将覆盖原有全部标签。' : '将清空目标记录的全部标签。' }}</p>
      <p v-if="batchAction === 'delete'" class="warning">将永久删除记录及其标签关联。此操作无法撤销。</p>
      <div v-if="batchPreview" class="batch-confirm"><strong>{{ batchPreview.count.toLocaleString() }}</strong><span>条提示词将执行「{{ actionLabel[batchAction] }}」</span><p v-if="batchPreview.tags.length">标签：{{ batchPreview.tags.join('、') }}</p></div>
      <p v-if="batchError" class="error-text" role="alert">{{ batchError }}</p>
      <div class="modal-actions"><button :disabled="batchBusy" @click="closeBatch">取消</button><button v-if="!batchPreview" class="primary" :disabled="batchBusy" @click="previewBatch">{{ batchBusy ? '正在核对…' : '预览影响范围' }}</button><button v-else :class="batchAction === 'delete' ? 'destructive' : 'primary'" :disabled="batchBusy" @click="applyBatch">{{ batchBusy ? '处理中…' : `确认${batchAction === 'delete' ? '删除' : '应用'}` }}</button></div>
    </ModalShell>
  </div>
  <KeepAlive>
    <ResourceLibrary v-if="activeResource !== 'prompts'" :key="activeResource" ref="resourcePage" :kind="activeResource" :theme="theme"
      @navigate="changeResource" @theme="theme = $event" @prompt="fromTemplate" />
  </KeepAlive>
</template>
