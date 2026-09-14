import type { InvestigationToolCall } from '../api'

const toolLabels: Record<string, string> = {
  get_inventory: 'Read inventory position',
  get_demand_forecast: 'Read demand forecast',
  list_open_purchase_orders: 'Check incoming purchase orders',
  get_supplier_terms: 'Read supplier terms',
  get_budget: 'Check purchase budget',
  get_storage_capacity: 'Check storage capacity',
  get_planning_policy: 'Read replenishment policy',
  get_purchase_order: 'Read purchase order',
  list_supplier_options: 'Compare supplier options',
  get_recent_sales: 'Read recent sales velocity',
}

const evidenceSources: Record<string, string> = {
  'mock-wms': 'warehouse record',
  'mock-forecaster': 'forecast service',
  'mock-erp': 'ERP record',
  'mock-supplier-portal': 'supplier portal',
  'mock-finance': 'finance record',
  'mock-facility': 'facility record',
  'mock-policy-service': 'planning policy',
  'mock-supplier-master': 'supplier master',
  'mock-pos': 'sales record',
}

export function toolAuditLabel(toolName?: string): string {
  return toolLabels[toolName ?? ''] ?? 'Run approved read tool'
}

export function readableError(code?: string | null): string {
  if (!code) return 'The tool did not return usable evidence.'
  const messages: Record<string, string> = {
    NOT_FOUND: 'No matching operational record was found.',
    UNAUTHORIZED: 'The request was outside this review’s approved scope.',
    INVALID_ARGUMENT: 'The tool request did not pass validation.',
    TOOL_NOT_ALLOWED: 'This tool is not approved for the investigation.',
    DUPLICATE_TOOL_CALL: 'This evidence was already requested in this round.',
    ATTEMPTS_EXHAUSTED: 'The retry limit for this evidence was reached.',
    TOOL_LIMIT_REACHED: 'The investigation call limit was reached.',
    POLICY_DENIED: 'The tool is not available in the current workflow phase.',
    INTERNAL: 'The evidence source could not be read safely.',
  }
  return messages[code] ?? `The tool failed safely (${code.replaceAll('_', ' ').toLowerCase()}).`
}

export function toolScope(arguments_: Record<string, unknown>): string {
  if ('purchase_order_id' in arguments_) return 'Approved purchase-order scope'
  if ('supplier_id' in arguments_) return 'Approved supplier and product scope'
  if ('product_id' in arguments_ && 'node_id' in arguments_) return 'Product and fulfilment-node scope'
  if ('node_id' in arguments_) return 'Fulfilment-node scope'
  return 'Current review scope'
}

export function toolOutcome(call: InvestigationToolCall): string {
  if (call.status !== 'SUCCEEDED') return readableError(call.error_code)
  return `Evidence captured from ${evidenceSources[call.result_ref ?? ''] ?? 'an approved source'}.`
}
