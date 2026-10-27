export type ResourceKind = 'templates' | 'skills'
export interface Variable {
  name: string; label: string; type: 'text' | 'textarea' | 'select' | 'number'
  description: string; required: boolean; default: string | number | null
  options: string[]; min: number | null; max: number | null
}
export interface ResourceFile { path: string; hash: string; size: number; media_type: string }
export interface FileChange { path: string; text?: string; base64?: string; remove?: boolean }
export interface ResourceInput {
  title: string; description: string; tags: string[]; target_models: string[]
  language: string; template_kind: 'assistant' | 'prompt'; body: string
  variables: Variable[]; context_paths: string[]; files: FileChange[]
}
export interface Resource extends Omit<ResourceInput, 'files'> {
  id: number; kind: ResourceKind; builtin: boolean; active: boolean; preset_id: string | null
  files: ResourceFile[]; source: { author?: string; type?: string; url?: string; commit?: string; reference_urls?: string[] }
  derived_from?: {id: number; preset_id: string; number: number}
  skill_name?: string; compatibility?: string
  version: string; number: number; created_at: string; updated_at: string
}
export interface ResourceSummary extends Omit<Resource, 'body' | 'variables' | 'files'> { excerpt: string; file_count: number }
export interface ResourcePage { items: ResourceSummary[]; total: number; target_models: string[] }
export interface ResourceVersion { number: number; version: string; created_at: string }
export const resourceInput = (record: Resource): ResourceInput => ({
  title: record.title, description: record.description, tags: [...record.tags],
  target_models: [...record.target_models], language: record.language,
  template_kind: record.template_kind, body: record.body,
  variables: JSON.parse(JSON.stringify(record.variables)), context_paths: [...record.context_paths], files: [],
})
export const blankResource = (kind: ResourceKind): ResourceInput => ({
  title: '', description: '', tags: [], target_models: [], language: 'zh', template_kind: 'assistant',
  body: kind === 'skills' ? '---\nname: custom-skill\ndescription: 描述这个技能的用途和适用场景。\n---\n\n# 新技能\n\n' : '',
  variables: [], context_paths: [], files: [],
})
