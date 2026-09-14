import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const { apiMock } = vi.hoisted(() => ({ apiMock: {
  recommendations: vi.fn(), startReview: vi.fn(), review: vi.fn(), events: vi.fn(),
  approve: vi.fn(), reject: vi.fn(), partialFixture: vi.fn(), confirmPartial: vi.fn(),
  resetScenario: vi.fn(), advanceScenario: vi.fn(),
} }))

vi.mock('./api', () => ({ api: apiMock }))

const rec = {
  id: 'rec-1', source_reference: 'REC-800', product: { id: 'p-1', sku: 'APL-001', name: 'Royal Gala Apples' },
  node: { id: 'n-1', code: 'BLR-01', name: 'Bengaluru FC' }, supplier: { id: 's-1', name: 'Fresh Farms' },
  recommended_quantity: 800, review_id: null, review_status: null,
}

describe('buyer decision workspace', () => {
  afterEach(cleanup)

  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.recommendations.mockResolvedValue({ items: [rec] })
    apiMock.events.mockResolvedValue({ items: [] })
  })

  it('starts an evidence review for the selected recommendation', async () => {
    apiMock.startReview.mockResolvedValue({ review_id: 'review-1', status: 'CREATED' })
    apiMock.review.mockResolvedValue({ id: 'review-1', status: 'COLLECTING_EVIDENCE', recommendation: rec, evidence: { items: [], completeness: 'PENDING', errors: [], snapshot_hash: null }, decision: null, approval: null, action: null, validation: null })
    render(<App />)
    await screen.findByRole('heading', { name: 'Royal Gala Apples' })
    fireEvent.click(screen.getByRole('button', { name: /investigate recommendation/i }))
    await waitFor(() => expect(apiMock.startReview).toHaveBeenCalledWith('rec-1'))
    expect(await screen.findByText('Investigating the purchasing situation')).toBeInTheDocument()
  })

  it('confirms the exact proposal before submitting approval', async () => {
    apiMock.startReview.mockResolvedValue({ review_id: 'review-2', status: 'CREATED' })
    apiMock.review.mockResolvedValue({
      id: 'review-2', status: 'AWAITING_APPROVAL', recommendation: rec,
      evidence: { items: [], completeness: 'COMPLETE', errors: [], snapshot_hash: 'hash' },
      decision: {
        version: 1, type: 'MODIFY', original_quantity: 800, proposed_quantity: 450,
        confidence: 'HIGH', reason_codes: ['NEED_LOWER_THAN_ORIGINAL'],
        calculations: { total_cost_minor: 562500, currency: 'INR', usable_on_hand: 150, inventory_position: 250, target_stock: 750, raw_need: 500 },
        constraints: [{ code: 'WITHIN_BUDGET', passed: true }],
        explanation: { summary: 'Modify from 800 to 450 units.', important_factors: [], constraint_summary: 'All checks pass.', uncertainties: [], next_action: 'Buyer approval is required.' },
      },
      approval: { status: 'PENDING', proposal_version: 1, requested_at: new Date().toISOString(), decided_at: null, comment: null },
      action: null, validation: null,
    })
    apiMock.approve.mockResolvedValue({ review_id: 'review-2', status: 'EXECUTING' })
    render(<App />)
    await screen.findByRole('heading', { name: 'Royal Gala Apples' })
    fireEvent.click(screen.getByRole('button', { name: /investigate recommendation/i }))
    await screen.findByRole('button', { name: /approve & create po/i })
    fireEvent.click(screen.getByRole('button', { name: /approve & create po/i }))
    const dialog = await screen.findByRole('dialog', { name: 'Confirm purchase order' })
    expect(dialog).toHaveTextContent('450 units')
    expect(dialog).toHaveTextContent('Proposal version 1')
    fireEvent.click(screen.getByRole('button', { name: /approve exact proposal/i }))
    await waitFor(() => expect(apiMock.approve).toHaveBeenCalledWith('review-2', 1))
  })

  it('prepares a resettable Scenario 1 safety case from the Scenario Lab', async () => {
    apiMock.resetScenario.mockResolvedValue({
      review_id: 'scenario-review', scenario: 'inventory-conflict', title: 'Conflicting inventory counts',
      purpose: 'Reserved plus damaged stock exceeds on-hand inventory.',
      expected_outcome: 'The review escalates to investigate; no purchase order can be created.',
      next_step: 'Inspect the conflicting Inventory evidence and failed policy check.', requires_advance: false,
    })
    apiMock.review.mockResolvedValue({ id: 'scenario-review', status: 'NEEDS_ATTENTION', recommendation: rec, evidence: { items: [], completeness: 'PARTIAL', errors: ['inventory quantities are inconsistent'], snapshot_hash: 'hash' }, decision: { version: 1, type: 'INVESTIGATE', original_quantity: 800, proposed_quantity: 0, confidence: 'LOW', reason_codes: ['CONFLICTING_EVIDENCE'], calculations: {}, constraints: [], explanation: { summary: 'Investigate.', important_factors: [], constraint_summary: 'Needs attention.', uncertainties: [], next_action: 'Resolve evidence.' } }, approval: null, action: null, validation: null })
    render(<App />)
    fireEvent.click((await screen.findAllByRole('button', { name: 'Inventory conflict' }))[0])
    await waitFor(() => expect(apiMock.resetScenario).toHaveBeenCalledWith('inventory-conflict'))
    expect(await screen.findByText('Conflicting inventory counts')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Guard confirmed')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /reset conflicting inventory counts testcase/i }))
    await waitFor(() => expect(apiMock.resetScenario).toHaveBeenCalledTimes(2))
  })

  it('starts a fresh review from a terminal seeded result', async () => {
    apiMock.startReview
      .mockResolvedValueOnce({ review_id: 'review-old', status: 'CREATED' })
      .mockResolvedValueOnce({ review_id: 'review-fresh', status: 'CREATED' })
    apiMock.review.mockResolvedValue({
      id: 'review-old', status: 'COMPLETED', recommendation: rec,
      evidence: { items: [], completeness: 'COMPLETE', errors: [], snapshot_hash: 'hash' },
      decision: {
        version: 1, type: 'REJECT', original_quantity: 800, proposed_quantity: 0,
        confidence: 'HIGH', reason_codes: ['NO_NET_REQUIREMENT'], calculations: {}, constraints: [],
        explanation: { summary: 'No order.', important_factors: [], constraint_summary: 'Passed.', uncertainties: [], next_action: 'None.' },
      },
      approval: null, action: null, actions: [], validation: null,
    })
    render(<App />)
    await screen.findByRole('heading', { name: 'Royal Gala Apples' })
    fireEvent.click(screen.getByRole('button', { name: /investigate recommendation/i }))
    const rerun = await screen.findByRole('button', { name: /run again with fresh facts/i })
    fireEvent.click(rerun)
    await waitFor(() => expect(apiMock.startReview).toHaveBeenNthCalledWith(2, 'rec-1'))
  })
})
