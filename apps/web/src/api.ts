export type Recommendation = {
  id: string
  source_reference: string
  product: { id: string; sku: string; name: string }
  node: { id: string; code: string; name: string }
  supplier: { id: string; name: string }
  recommended_quantity: number
  review_id: string | null
  review_status: string | null
}

export type Review = {
  id: string
  status: string
  recovery_attempts?: number
  recommendation: Recommendation
  evidence: { items: Evidence[]; completeness: string; snapshot_hash: string | null; errors: string[] }
  decision: null | {
    version: number
    type: string
    original_quantity: number
    proposed_quantity: number
    confidence: string
    reason_codes: string[]
    calculations: Record<string, unknown>
    constraints: { code: string; passed: boolean; observed?: unknown; limit?: unknown }[]
    explanation: { summary: string; important_factors: { reason_code: string; evidence_refs: string[] }[]; constraint_summary: string; uncertainties: string[]; next_action: string }
  }
  approval: null | { status: string; proposal_version: number; requested_at: string; decided_at: string | null; comment: string | null }
  action: null | { status: string; purchase_order_id: string | null; external_id: string | null; total_minor: number | null; currency: string | null }
  validation: null | { status: string; comparisons: { field: string; expected: unknown; actual: unknown }[]; mismatches: string[] }
}

export type Evidence = { name: string; status: string; source: string; observed_at: string; effective_from?: string | null; effective_until?: string | null; value: unknown; version: string | null }
export type TimelineEvent = { id: number; event_type: string; actor_type: string; created_at: string; payload: Record<string, unknown> }
export type DemoScenario = {
  review_id: string
  scenario: string
  title: string
  purpose: string
  expected_outcome: string
  next_step: string
  requires_advance: boolean
}

const API = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', 'X-Demo-Role': 'BUYER', ...init?.headers },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map((item: { msg?: string }) => item.msg ?? 'Invalid request.').join(' ')
      : payload.detail?.detail ?? payload.detail
    throw new Error(detail ?? payload.title ?? `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  recommendations: () => request<{ items: Recommendation[] }>('/recommendations'),
  startReview: (recommendation_id: string) => request<{ review_id: string; status: string }>('/reviews', { method: 'POST', body: JSON.stringify({ recommendation_id }), headers: { 'Idempotency-Key': crypto.randomUUID() } }),
  review: (id: string) => request<Review>(`/reviews/${id}`),
  events: (id: string) => request<{ items: TimelineEvent[] }>(`/reviews/${id}/events`),
  approve: (id: string, proposal_version: number) => request(`/reviews/${id}/approval`, { method: 'POST', body: JSON.stringify({ proposal_version, decision: 'APPROVE', comment: 'Reviewed proposal and constraints' }) }),
  reject: (id: string, proposal_version: number, comment: string) => request(`/reviews/${id}/approval`, { method: 'POST', body: JSON.stringify({ proposal_version, decision: 'REJECT', comment }) }),
  partialFixture: () => request<{ purchase_order_id: string; product_id: string; ordered_quantity: number; confirmed_quantity: number }>('/demo/partial-fulfilment'),
  confirmPartial: (data: { purchase_order_id: string; product_id: string; confirmed_quantity: number }) => request<{ review_id: string }>('/mock/supplier-confirmations', { method: 'POST', headers: { 'X-Demo-Role': 'DEMO_ADMIN' }, body: JSON.stringify({ ...data, external_event_id: `DEMO-${crypto.randomUUID()}`, event_at: new Date().toISOString() }) }),
  resetScenario: (scenario: string) => request<DemoScenario>(`/demo/scenarios/${scenario}/reset`, { method: 'POST' }),
  advanceScenario: (scenario: string, review_id: string) => request<{ next_step: string }>(`/demo/scenarios/${scenario}/advance`, { method: 'POST', body: JSON.stringify({ review_id }) }),
}
