<script setup lang="ts">
import type { Variable } from '../resources/types'
const props = defineProps<{ modelValue: Variable[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Variable[]] }>()
function update(index: number, key: keyof Variable, value: unknown) {
  emit('update:modelValue', props.modelValue.map((item, i) => i === index ? { ...item, [key]: value } : item))
}
function add() {
  let index = props.modelValue.length + 1
  while (props.modelValue.some(v => v.name === `variable_${index}`)) index++
  emit('update:modelValue', [...props.modelValue, { name: `variable_${index}`, label: '新变量', type: 'textarea', description: '', required: true, default: null, options: [], min: null, max: null }])
}
</script>
<template>
  <div class="variable-editor"><div class="section-label">变量定义 <button type="button" :disabled="disabled" @click="add">＋ 添加变量</button></div>
    <p v-if="!modelValue.length" class="muted small">正文可使用双花括号变量，例如 <code v-pre>{{subject}}</code>。</p>
    <fieldset v-for="(variable, index) in modelValue" :key="index" :disabled="disabled" class="variable-definition">
      <div class="metadata-grid"><label>变量名<input :value="variable.name" :aria-label="`变量名 ${index + 1}`" @input="update(index, 'name', ($event.target as HTMLInputElement).value)"></label><label>显示名称<input :value="variable.label" @input="update(index, 'label', ($event.target as HTMLInputElement).value)"></label>
      <label>输入类型<select :value="variable.type" @change="update(index, 'type', ($event.target as HTMLSelectElement).value)"><option value="text">短文本</option><option value="textarea">长文本</option><option value="select">下拉选择</option><option value="number">数字</option></select></label><label>默认值<input :value="variable.default ?? ''" @input="update(index, 'default', ($event.target as HTMLInputElement).value || null)"></label></div>
      <label v-if="variable.type === 'select'" class="stack-label">选项（每行一个）<textarea :value="variable.options.join('\n')" rows="3" @input="update(index, 'options', ($event.target as HTMLTextAreaElement).value.split('\n').map(s => s.trim()).filter(Boolean))"></textarea></label>
      <div v-if="variable.type === 'number'" class="metadata-grid"><label>最小值<input type="number" :value="variable.min ?? ''" @input="update(index, 'min', ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value))"></label><label>最大值<input type="number" :value="variable.max ?? ''" @input="update(index, 'max', ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value))"></label></div>
      <label class="stack-label">说明<input :value="variable.description" @input="update(index, 'description', ($event.target as HTMLInputElement).value)"></label>
      <div class="variable-footer"><label class="check-label"><input type="checkbox" :checked="variable.required" @change="update(index, 'required', ($event.target as HTMLInputElement).checked)">必填</label><button type="button" class="text-button danger" @click="emit('update:modelValue', modelValue.filter((_, i) => i !== index))">移除变量</button></div>
    </fieldset>
  </div>
</template>
