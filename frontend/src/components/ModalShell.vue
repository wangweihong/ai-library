<script setup lang="ts">
import { onMounted, ref, useId } from 'vue'
defineProps<{ title: string, busy?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const titleId = useId()
onMounted(() => dialog.value?.showModal())
</script>
<template>
  <dialog ref="dialog" class="modal" :aria-labelledby="titleId" @cancel.prevent="!busy && emit('close')">
    <header class="modal-head"><h2 :id="titleId">{{ title }}</h2><button :disabled="busy" aria-label="关闭对话框" @click="emit('close')">×</button></header>
    <slot />
  </dialog>
</template>
