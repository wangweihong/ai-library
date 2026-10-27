export interface PromptInput {
  prompt: string; language: string; media_type: string; prompt_type: string
  nsfw: boolean; content_format: string; dynamic: boolean
  width: number; height: number; tags: string[]
}
export interface Prompt extends PromptInput {
  id: number; version: string; shasum: string; created_at: string
}
export interface Summary extends Omit<Prompt, 'prompt' | 'version' | 'shasum'> { excerpt: string }
export interface Filters {
  q: string; language: string; media_type: string; prompt_type: string
  safety: string; content_format: string; dynamic: string
  match_size: boolean; width: number; height: number; tags: string[]; untagged: boolean
}
export interface Tag { id: number; name: string; count: number }
export interface TagResult { items: Tag[]; total: number; untagged: number }
export interface Page { items: Summary[]; total: number; page: number; page_size: number; revision: string }
export interface Selection { mode: 'ids' | 'filter'; ids: number[]; filters: Filters }
export type BatchAction = 'append' | 'remove' | 'replace' | 'delete'
export interface BatchRequest { selection: Selection; action: BatchAction; tags: string[] }
export interface BatchPreview { count: number; action: BatchAction; tags: string[]; token: string; expires_at: number }

export const emptyPrompt = (): PromptInput => ({
  prompt: '', language: 'zh', media_type: 'image', prompt_type: 'natural', nsfw: false,
  content_format: 'plaintext', dynamic: false, width: 1024, height: 768, tags: [],
})
export const emptyFilters = (): Filters => ({
  q: '', language: '全部', media_type: '全部', prompt_type: '全部', safety: '全部',
  content_format: '全部', dynamic: '全部', match_size: false, width: 1024, height: 768,
  tags: [], untagged: false,
})
export function inputOf(record: Prompt): PromptInput {
  const fields = emptyPrompt()
  for (const key of Object.keys(fields) as (keyof PromptInput)[]) {
    Object.assign(fields, { [key]: record[key] })
  }
  return { ...fields, nsfw: Boolean(fields.nsfw), dynamic: Boolean(fields.dynamic), tags: [...fields.tags] }
}
export const fieldOptions = [
  { key: 'language', label: '语言', values: ['zh', 'en'] },
  { key: 'media_type', label: '媒体类型', values: ['image', 'video', 'audio', 'music'] },
  { key: 'prompt_type', label: '提示词类型', values: ['natural', 'danbooru'] },
  { key: 'content_format', label: '内容格式', values: ['plaintext', 'json'] },
] as const
export const labels: Record<string, string> = {
  zh: '中文', en: '英文', image: '图像', video: '视频', audio: '音频', music: '音乐',
  natural: '自然语言', danbooru: 'Danbooru', plaintext: '纯文本', json: 'JSON',
  safe: '安全', nsfw: 'NSFW', dynamic: '动态', static: '静态',
}
