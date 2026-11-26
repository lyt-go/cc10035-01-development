<template>
  <section class="page" data-module="trial_receipt">
    <header class="page-head">
      <div>
        <h2>报文解码 · 试投回单</h2>
        <p class="page-desc">
          录入原始帧与拟用解码规则，逐条审阅字段解释后向消息路由签发试投；
          一张回单只覆盖勾选帧，未勾选帧保留在草稿中。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          新建试投回单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">回单总数</span>
        <strong class="stat-value">{{ receipts.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">草稿</span>
        <strong class="stat-value">{{ countOf('草稿') }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已确认可投 / 需要重审</span>
        <strong class="stat-value">{{ countOf('已确认可投') }} / {{ countOf('需要重审') }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已发送</span>
        <strong class="stat-value">{{ countOf('已发送') }}</strong>
      </article>
    </div>

    <!-- 回单清单 -->
    <table class="data-table">
      <thead>
        <tr>
          <th>回单编号</th>
          <th>操作人</th>
          <th>选中帧 / 草稿帧</th>
          <th>当前状态</th>
          <th>仍待办理步骤</th>
          <th>投递编号</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="receipt in receipts"
          :key="receipt.id"
          :data-testid="`receipt-row-${receipt.id}`"
          :data-status="receipt.status"
        >
          <td>{{ receipt.回单编号 }}</td>
          <td>{{ receipt.操作人 }}</td>
          <td>{{ receipt.selected_count }} / {{ receipt.unselected_count }}</td>
          <td>
            <span class="status-tag" :class="statusClass(receipt.status)">{{ receipt.status }}</span>
            <span v-if="receipt.conflict_rule" class="conflict-hint">
              （规则 {{ receipt.conflict_rule }} 停用冲突）
            </span>
          </td>
          <td>{{ receipt.待办步骤 }}</td>
          <td>{{ receipt.delivery_id ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`open-${receipt.id}`" @click="openDetail(receipt.id)">
              打开回单
            </button>
          </td>
        </tr>
        <tr v-if="!receipts.length">
          <td colspan="7" class="empty-state">暂无试投回单，先新建一张草稿回单</td>
        </tr>
      </tbody>
    </table>

    <!-- 新建回单弹窗 -->
    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal wide" @submit.prevent="submitCreate">
        <h3>新建试投回单</h3>
        <label class="create-field">
          <span>值班操作人</span>
          <input v-model="createForm.operator" data-testid="create-operator" required placeholder="例如：值班员甲" />
        </label>
        <label class="create-field">
          <span>试投源 Topic（用于匹配消息路由）</span>
          <input v-model="createForm.source_topic" data-testid="create-topic" required />
        </label>

        <div class="frame-editor" v-for="(frame, index) in createForm.frames" :key="index">
          <div class="frame-editor-head">
            <strong>原始帧 {{ index + 1 }}</strong>
            <button
              v-if="createForm.frames.length > 1"
              class="link danger"
              type="button"
              @click="createForm.frames.splice(index, 1)"
            >
              移除
            </button>
          </div>
          <label class="create-field">
            <span>原始帧（hex）</span>
            <input v-model="frame.raw_hex" required placeholder="例如：AA00" />
          </label>
          <label class="create-field">
            <span>拟用解码规则</span>
            <select v-model="frame.rule_code" required>
              <option value="" disabled>请选择规则</option>
              <option v-for="rule in rules" :key="rule.rule_code" :value="rule.rule_code">
                {{ rule.rule_code }}（{{ rule.data_type }}，{{ rule.status }}）
              </option>
            </select>
          </label>
        </div>
        <button class="btn" type="button" @click="addDraftFrame">再加一帧</button>

        <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
        <div class="page-actions modal-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '生成草稿并解释字段' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 回单详情工作台 -->
    <div v-if="active" class="detail-panel" data-testid="receipt-detail">
      <header class="detail-head">
        <div>
          <h3>{{ active.回单编号 }}
            <span class="status-tag" :class="statusClass(active.status)">{{ active.status }}</span>
          </h3>
          <p class="page-desc">
            操作人 {{ active.操作人 }} ｜ 源 Topic {{ active.source_topic }} ｜ 创建于 {{ active.创建时间 }}
          </p>
          <p v-if="active.conflict_rule" class="conflict-banner" data-testid="conflict-banner">
            规则 {{ active.conflict_rule }} 在准备期间被停用，与本笔尚未发送的试投冲突，
            最终发送的解释须由 {{ active.操作人 }} 重审确认。
          </p>
        </div>
        <button class="btn ghost" type="button" @click="active = null">关闭详情</button>
      </header>

      <!-- 仍待办理步骤清单 -->
      <ol class="todo-list" data-testid="todo-list">
        <li v-for="step in todoSteps" :key="step.label" :class="{ done: step.done, current: step.current }">
          <span class="todo-state">{{ step.done ? '✓' : step.current ? '●' : '○' }}</span>
          <span>{{ step.label }}</span>
        </li>
      </ol>

      <!-- 帧与字段解释 -->
      <table class="data-table frame-table">
        <thead>
          <tr>
            <th>勾选</th>
            <th>帧编号</th>
            <th>原始帧</th>
            <th>拟用规则</th>
            <th>字段解释（须逐条看过）</th>
            <th>审阅状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="frame in active.frames" :key="frame.frame_id" :data-testid="`frame-row-${frame.frame_id}`">
            <td>
              <input
                type="checkbox"
                :checked="frame.selected"
                :disabled="active.status === '已发送'"
                :data-testid="`select-${frame.frame_id}`"
                @change="toggleSelection(frame, ($event.target as HTMLInputElement).checked)"
              />
            </td>
            <td>{{ frame.frame_id }}</td>
            <td><code>{{ frame.raw_hex }}</code></td>
            <td>
              {{ frame.rule_code }}
              <select
                v-if="active.status === '需要重审' && frame.selected"
                :value="frame.rule_code"
                :data-testid="`rule-switch-${frame.frame_id}`"
                @change="switchRule(frame, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="rule in switchableRules" :key="rule.rule_code" :value="rule.rule_code">
                  改用 {{ rule.rule_code }}（{{ rule.data_type }}）
                </option>
              </select>
            </td>
            <td class="interpretation">
              <p class="interpret-text" :data-testid="`interpretation-${frame.frame_id}`">
                {{ frame.interpretation.explanation }}
              </p>
              <ul class="interpret-meta">
                <li>字段 hex：{{ frame.interpretation.field_hex }}</li>
                <li>解释类型：{{ frame.interpretation.value_type }}</li>
                <li>解释值：{{ frame.interpretation.parsed_value }}</li>
                <li v-if="frame.interpretation_changed" class="changed">解释已因换规则发生变化，请重新确认</li>
              </ul>
            </td>
            <td>
              <span v-if="frame.reviewed" class="reviewed">
                已由 {{ frame.confirmed_by }} 审阅（{{ frame.reviewed_at }}）
              </span>
              <span v-else-if="frame.selected" class="pending-review">待逐条审阅</span>
              <span v-else class="muted">未勾选，保留在草稿</span>
            </td>
            <td>
              <button
                v-if="frame.selected && !frame.reviewed && active.status !== '已发送'"
                class="btn small"
                type="button"
                :data-testid="`review-${frame.frame_id}`"
                @click="doReview(frame)"
              >
                我已看过解释，审阅确认
              </button>
              <button
                v-if="active.status === '需要重审' && frame.selected"
                class="btn small warning"
                type="button"
                :data-testid="`rereview-${frame.frame_id}`"
                @click="doReReview(frame)"
              >
                重审并再次确认
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 工作流动作 -->
      <div class="page-actions workflow-actions">
        <button
          class="btn primary"
          type="button"
          data-testid="confirm-receipt"
          :disabled="!canConfirm || busy"
          @click="doConfirm"
        >
          确认试投（等待路由答复）
        </button>
        <button
          class="btn primary"
          type="button"
          data-testid="send-trial"
          :disabled="!canSend || busy"
          @click="doSend"
        >
          {{ busy ? '发送中…（按钮恢复可点也只会复用同一次投递）' : '签发试投' }}
        </button>
        <span class="token-hint" v-if="active.client_token">
          申请凭证：{{ active.client_token }}（重复提交按原申请幂等）
        </span>
      </div>

      <!-- 路由投递详情 -->
      <section v-if="deliveries.length" class="delivery-panel" data-testid="delivery-panel">
        <h4>路由详情（实际投递）</h4>
        <article v-for="delivery in deliveries" :key="delivery.delivery_id" class="delivery-card">
          <p>
            投递编号 <strong>{{ delivery.delivery_id }}</strong>
            ｜ 路由规则 {{ delivery.router_code ?? '未匹配' }}
            ｜ 实际目的地 <strong>{{ delivery.target_topic }}</strong>
            ｜ 转发方式 {{ delivery.forward_method }}
            ｜ 投递时间 {{ delivery.delivered_at }}
          </p>
          <table class="data-table">
            <thead>
              <tr><th>帧</th><th>原始帧</th><th>最终规则</th><th>解释类型</th><th>解释值</th><th>确认人</th></tr>
            </thead>
            <tbody>
              <tr v-for="dframe in delivery.frames" :key="dframe.frame_id">
                <td>{{ dframe.frame_id }}</td>
                <td><code>{{ dframe.raw_hex }}</code></td>
                <td>{{ dframe.rule_code }}</td>
                <td>{{ dframe.value_type }}</td>
                <td>{{ dframe.parsed_value }}</td>
                <td>{{ dframe.confirmed_by }}</td>
              </tr>
            </tbody>
          </table>
        </article>
      </section>
    </div>

    <footer class="page-foot">
      <span>状态口径：草稿 / 已确认可投 / 需要重审 / 已发送；未勾选帧始终保留在草稿中</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  confirmReceipt,
  createReceipt,
  fetchDeliveries,
  fetchReceipt,
  fetchReceipts,
  fetchRules,
  reReviewFrame,
  reviewFrame,
  selectFrame,
  sendTrial,
} from '@/api/trial-receipt'
import type { Delivery, RuleOption, TrialReceipt, ReceiptFrame } from '@/api/trial-receipt'

const receipts = ref<TrialReceipt[]>([])
const rules = ref<RuleOption[]>([])
const active = ref<TrialReceipt | null>(null)
const deliveries = ref<Delivery[]>([])
const errorMessage = ref('')
const busy = ref(false)
const creating = ref(false)
const showCreate = ref(false)
// 回单首次签发时生成的申请凭证：发送失败重试或按钮恢复可点后重提都复用它，
// 保证浏览器用原申请再次确认只能得到同一次路由投递。
const clientTokens = new Map<number, string>()

const createForm = ref({
  operator: '',
  source_topic: 'trial/device/dev-01/temp',
  frames: [{ raw_hex: 'AA00', rule_code: 'R1' }],
})

const switchableRules = computed(() =>
  rules.value.filter((rule) => rule.status !== '已停用'),
)

const selectedFrames = computed(() =>
  active.value ? active.value.frames.filter((frame) => frame.selected) : [],
)
const allSelectedReviewed = computed(
  () => selectedFrames.value.length > 0 && selectedFrames.value.every((frame) => frame.reviewed),
)
const canConfirm = computed(
  () =>
    active.value !== null &&
    active.value.status !== '已发送' &&
    active.value.status !== '需要重审' &&
    allSelectedReviewed.value,
)
const canSend = computed(
  () =>
    active.value !== null &&
    active.value.status !== '已发送' &&
    active.value.status !== '需要重审' &&
    allSelectedReviewed.value,
)

const todoSteps = computed(() => {
  if (!active.value) return []
  const receipt = active.value
  const hasSelection = selectedFrames.value.length > 0
  const reviewed = allSelectedReviewed.value
  const confirmed = ['已确认可投', '已发送'].includes(receipt.status)
  const sent = receipt.status === '已发送'
  return [
    { label: '录入原始帧与拟用解码规则，生成草稿回单', done: true, current: false },
    { label: '勾选纳入本回单的帧（未勾选帧保留在草稿）', done: hasSelection, current: !hasSelection },
    { label: '逐条看过字段解释并审阅确认', done: reviewed, current: hasSelection && !reviewed },
    {
      label: receipt.conflict_rule
        ? `规则 ${receipt.conflict_rule} 停用冲突，改用规则重审并再次确认`
        : '确认试投，等待路由答复',
      done: confirmed,
      current: reviewed && !confirmed,
    },
    { label: '签发试投并取得可追踪目的地的回单', done: sent, current: confirmed && !sent },
  ]
})

function countOf(status: string): number {
  return receipts.value.filter((receipt) => receipt.status === status).length
}

function statusClass(status: string): string {
  if (status === '已发送') return 'st-sent'
  if (status === '需要重审') return 'st-review'
  if (status === '已确认可投') return 'st-confirmed'
  return 'st-draft'
}

function openCreate() {
  createForm.value = {
    operator: '',
    source_topic: 'trial/device/dev-01/temp',
    frames: [{ raw_hex: 'AA00', rule_code: 'R1' }],
  }
  errorMessage.value = ''
  showCreate.value = true
}

function addDraftFrame() {
  createForm.value.frames.push({ raw_hex: '', rule_code: '' })
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createReceipt({
      operator: createForm.value.operator,
      source_topic: createForm.value.source_topic,
      frames: createForm.value.frames,
    })
    if (!result.ok || !result.entry) throw new Error(result.message)
    showCreate.value = false
    await reload()
    await openDetail(result.entry.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回单创建失败'
  } finally {
    creating.value = false
  }
}

async function openDetail(id: number) {
  errorMessage.value = ''
  try {
    active.value = await fetchReceipt(id)
    deliveries.value = await fetchDeliveries(id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回单详情读取失败'
  }
}

async function refreshActive() {
  if (!active.value) return
  const id = active.value.id
  active.value = await fetchReceipt(id)
  deliveries.value = await fetchDeliveries(id)
  await reload()
}

async function runWorkflow(action: () => Promise<{ ok: boolean; message: string }>) {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await action()
    if (!result.ok) throw new Error(result.message)
    await refreshActive()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
    await refreshActive().catch(() => undefined)
  } finally {
    busy.value = false
  }
}

function toggleSelection(frame: ReceiptFrame, selected: boolean) {
  if (!active.value) return
  return runWorkflow(() =>
    selectFrame(active.value!.id, frame.frame_id, selected).then((r) => ({
      ok: r.ok,
      message: r.message,
    })),
  )
}

function doReview(frame: ReceiptFrame) {
  if (!active.value) return
  return runWorkflow(() =>
    reviewFrame(active.value!.id, frame.frame_id, active.value!.操作人).then((r) => ({
      ok: r.ok,
      message: r.message,
    })),
  )
}

function doReReview(frame: ReceiptFrame) {
  if (!active.value) return
  return runWorkflow(() =>
    reReviewFrame(
      active.value!.id,
      frame.frame_id,
      active.value!.操作人,
      frame.rule_code,
    ).then((r) => ({ ok: r.ok, message: r.message })),
  )
}

function switchRule(frame: ReceiptFrame, newRuleCode: string) {
  if (!active.value) return
  return runWorkflow(() =>
    reReviewFrame(
      active.value!.id,
      frame.frame_id,
      active.value!.操作人,
      newRuleCode,
    ).then((r) => ({ ok: r.ok, message: r.message })),
  )
}

function doConfirm() {
  if (!active.value) return
  return runWorkflow(() =>
    confirmReceipt(active.value!.id, active.value!.操作人).then((r) => ({
      ok: r.ok,
      message: r.message,
    })),
  )
}

async function doSend() {
  if (!active.value) return
  const receipt = active.value
  // 申请凭证优先级：回单已绑定的在途凭证 → 本次页面会话生成的凭证 → 新生成。
  // 任何重试/重复点击都复用同一凭证，后端据此保证只产生一次路由投递。
  let clientToken = receipt.client_token ?? clientTokens.get(receipt.id)
  if (!clientToken) {
    clientToken = `apply-${receipt.回单编号}-${Date.now().toString(36)}`
    clientTokens.set(receipt.id, clientToken)
  }
  await runWorkflow(() =>
    sendTrial(receipt.id, receipt.操作人, clientToken as string).then((r) => ({
      ok: r.ok,
      message: r.message,
    })),
  )
}

async function reload() {
  receipts.value = await fetchReceipts()
}

onMounted(async () => {
  rules.value = await fetchRules()
  await reload()
})
</script>

<style scoped>
.modal.wide { width: min(760px, 100%); }
.frame-editor { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }
.frame-editor-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.link.danger { color: #b42318; }
.modal-actions { margin-top: 14px; }
.detail-panel { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin-top: 16px; }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.status-tag { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; margin-left: 6px; }
.st-draft { background: #e2e8f0; color: #334155; }
.st-confirmed { background: #dbeafe; color: #1d4ed8; }
.st-review { background: #fef3c7; color: #b45309; }
.st-sent { background: #dcfce7; color: #15803d; }
.conflict-hint { color: #b45309; font-size: 12px; }
.conflict-banner { background: #fffbeb; border: 1px solid #f59e0b; color: #92400e; padding: 8px 10px; border-radius: 6px; font-size: 13px; }
.todo-list { margin: 12px 0; padding-left: 0; list-style: none; display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; }
.todo-list li { display: flex; gap: 6px; align-items: center; font-size: 13px; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: #f8fafc; }
.todo-list li.done { background: #f0fdf4; border-color: #86efac; }
.todo-list li.current { background: #eff6ff; border-color: #93c5fd; }
.todo-state { font-weight: 700; }
.frame-table { margin-top: 8px; }
.interpretation { min-width: 260px; }
.interpret-text { margin: 0 0 4px; font-weight: 600; }
.interpret-meta { margin: 0; padding-left: 16px; color: var(--muted); font-size: 12px; }
.interpret-meta .changed { color: #b45309; font-weight: 600; list-style: none; margin-left: -10px; }
.reviewed { color: #15803d; font-size: 12px; }
.pending-review { color: #b45309; font-size: 12px; }
.muted { color: var(--muted); font-size: 12px; }
.btn.small { padding: 4px 8px; font-size: 12px; }
.btn.warning { border-color: #f59e0b; color: #b45309; }
.workflow-actions { margin: 14px 0; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.token-hint { color: var(--muted); font-size: 12px; }
.delivery-panel { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 12px; }
.delivery-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }
select { padding: 6px; border: 1px solid var(--border); border-radius: 6px; }
</style>
