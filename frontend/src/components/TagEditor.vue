<script setup lang="ts">
import { ref } from 'vue'
import type { Tag } from '../resources/prompts/types'

const props = defineProps<{ modelValue: string[], suggestions: Tag[], label: string, disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]], search: [value: string], pending: [value: boolean] }>()
const text = ref('')
const focused = ref(false)
function add(value = text.value) {
  const tags = value.split(',').map(t => t.trim().toLowerCase()).filter(Boolean)
  if (!tags.length) return
  emit('update:modelValue', [...new Set([...props.modelValue, ...tags])])
  text.value = ''
  emit('pending', false)
  emit('search', '')
}
defineExpose({ flush: () => add() })
</script>

<template>
  <div class="tag-editor">
    <div class="chips">
      <button v-for="tag in modelValue" :key="tag" type="button" class="chip selected" :disabled="disabled"
        :aria-label="`移除标签 ${tag}`" @click="emit('update:modelValue', modelValue.filter(t => t !== tag))">{{ tag }} <span aria-hidden="true">×</span></button>
    </div>
    <label class="sr-only">{{ label }}</label>
    <div class="tag-input-row">
      <input v-model="text" :aria-label="label" placeholder="输入标签，回车添加" :disabled="disabled"
        @focus="focused = true" @blur="focused = false; add()" @input="emit('search', text); emit('pending', !!text.trim())"
        @keydown.enter.prevent.stop="add()" @keydown.comma.prevent="add()">
      <button type="button" :disabled="disabled || !text.trim()" @click="add()">添加</button>
    </div>
    <div v-if="focused && text.trim()" class="suggestions" aria-label="标签建议">
      <button v-for="tag in suggestions.filter(t => t.name.includes(text.toLowerCase()) && !modelValue.includes(t.name)).slice(0, 8)" :key="tag.id"
        type="button" @mousedown.prevent @click="add(tag.name)">{{ tag.name }} <span class="muted">{{ tag.count }}</span></button>
    </div>
  </div>
</template>
