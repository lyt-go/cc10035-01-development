/**
 * 试投回单工作流接口：与后端 /api/trial-receipt 对齐。
 * 回单含嵌套帧与字段解释，状态机由后端领域服务校验，前端只做呈现与提交。
 */
import { request } from './client'
import type { ActionResult } from '@/data/types'

export type Interpretation = {
  ok: boolean
  rule_code: string
  rule_status: string
  data_type: string
  value_type: string
  parsed_value: number | string
  field_hex: string
  field_offset: number
  field_length: number
  byte_order: string
  explanation: string
}

export type ReceiptFrame = {
  frame_id: string
  raw_hex: string
  rule_code: string
  selected: boolean
  reviewed: boolean
  confirmed_by: string | null
  reviewed_at: string | null
  interpretation: Interpretation
  interpretation_changed?: boolean
}

export type TrialReceipt = {
  id: number
  回单编号: string
  操作人: string
  创建时间: string
  source_topic: string
  status: string
  pending: boolean
  abnormal: boolean
  frames: ReceiptFrame[]
  selected_count: number
  unselected_count: number
  待办步骤: string
  conflict_rule: string | null
  confirmed_at: string | null
  sent_at: string | null
  delivery_id: string | null
  client_token: string | null
}

export type RuleOption = {
  rule_code: string
  protocol_type: string
  data_type: string
  status: string
}

export type DeliveryFrame = {
  frame_id: string
  raw_hex: string
  rule_code: string
  value_type: string
  parsed_value: number | string
  confirmed_by: string | null
}

export type Delivery = {
  id: number
  delivery_id: string
  receipt_id: number
  回单编号: string
  client_token: string
  source_topic: string
  router_code: string | null
  target_topic: string
  forward_method: string
  frames: DeliveryFrame[]
  frame_count: number
  delivered_at: string
}

async function post(path: string, body: unknown): Promise<ActionResult & { entry?: TrialReceipt }> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return await response.json()
}

export async function fetchRules(): Promise<RuleOption[]> {
  const response = await request('/api/trial-receipt/rules')
  if (!response.ok) throw new Error('解码规则读取失败')
  const payload = await response.json()
  return payload.items as RuleOption[]
}

export async function fetchReceipts(): Promise<TrialReceipt[]> {
  const response = await request('/api/trial_receipt?size=200')
  if (!response.ok) throw new Error('试投回单读取失败')
  const payload = await response.json()
  return payload.items as TrialReceipt[]
}

export async function fetchReceipt(id: number): Promise<TrialReceipt> {
  const response = await request(`/api/trial_receipt/${id}`)
  if (!response.ok) throw new Error('回单详情读取失败')
  return await response.json()
}

export async function createReceipt(input: {
  operator: string
  source_topic: string
  frames: { raw_hex: string; rule_code: string }[]
}) {
  return post('/api/trial-receipt', input)
}

export function selectFrame(id: number, frameId: string, selected: boolean) {
  return post(`/api/trial-receipt/${id}/frames/selection`, { frame_id: frameId, selected })
}

export function reviewFrame(id: number, frameId: string, operator: string) {
  return post(`/api/trial-receipt/${id}/frames/review`, { frame_id: frameId, operator })
}

export function reReviewFrame(
  id: number,
  frameId: string,
  operator: string,
  newRuleCode: string | null,
) {
  return post(`/api/trial-receipt/${id}/frames/re-review`, {
    frame_id: frameId,
    operator,
    new_rule_code: newRuleCode,
  })
}

export function confirmReceipt(id: number, operator: string) {
  return post(`/api/trial-receipt/${id}/confirm`, { operator })
}

export function sendTrial(id: number, operator: string, clientToken: string) {
  return post(`/api/trial-receipt/${id}/send`, {
    operator,
    client_token: clientToken,
  })
}

export async function fetchDeliveries(id: number): Promise<Delivery[]> {
  const response = await request(`/api/trial-receipt/${id}/deliveries`)
  if (!response.ok) throw new Error('路由投递详情读取失败')
  const payload = await response.json()
  return payload.items as Delivery[]
}
