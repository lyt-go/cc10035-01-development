<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "设备注册", "created": 0, "pending": 0, "abnormal": 0}, {"name": "协议配置", "created": 0, "pending": 0, "abnormal": 0}, {"name": "报文解码", "created": 0, "pending": 0, "abnormal": 0}, {"name": "消息路由", "created": 0, "pending": 0, "abnormal": 0}, {"name": "时序测点", "created": 0, "pending": 0, "abnormal": 0}, {"name": "时序存储", "created": 0, "pending": 0, "abnormal": 0}, {"name": "降采样", "created": 0, "pending": 0, "abnormal": 0}, {"name": "告警触发", "created": 0, "pending": 0, "abnormal": 0}, {"name": "固件升级", "created": 0, "pending": 0, "abnormal": 0}, {"name": "网关管理", "created": 0, "pending": 0, "abnormal": 0}, {"name": "设备影子", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据断点", "created": 0, "pending": 0, "abnormal": 0}, {"name": "批量导出", "created": 0, "pending": 0, "abnormal": 0}, {"name": "规则引擎", "created": 0, "pending": 0, "abnormal": 0}, {"name": "边缘缓存", "created": 0, "pending": 0, "abnormal": 0}, {"name": "标签绑定", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据流管道", "created": 0, "pending": 0, "abnormal": 0}, {"name": "聚合分发", "created": 0, "pending": 0, "abnormal": 0}]
  }
})
</script>
