import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, AlertTriangle, ArrowRight, Boxes,
  Check, CheckCircle2, ChevronDown, CircleHelp, Clock3, FileCheck2, FileText,
  FlaskConical, LayoutDashboard, LoaderCircle, MapPin, PackageCheck, PackageSearch, RefreshCw,
  ShieldCheck, Sparkles, Truck, Warehouse, X,
} from 'lucide-react'
import {
  api,
  type ActionItem,
  type DemoScenario,
  type Evidence,
  type Recommendation,
  type Review,
  type TimelineEvent,
} from './api'
import { CustomScenarioModal } from './components/CustomScenarioModal'
import { ExecutionRetryBanner } from './components/ExecutionRetryBanner'
import { SourcingPlanView } from './components/SourcingPlanView'
import { ToolTraceDrawer } from './components/ToolTraceDrawer'
import { readableError, toolAuditLabel } from './components/toolAudit'

const scenarioChoices = [
  ['inventory-conflict', 'Inventory conflict'],
  ['late-incoming-po', 'Delayed incoming PO'],
  ['supplier-terms-change', 'Supplier quote change'],
  ['lost-create-response', 'Lost create response'],
  ['wrong-persisted-po', 'Wrong persisted PO'],
  ['multi-supplier-shortfall', 'Supplier shortfall (multi-supplier)'],
  ['demand-spike', 'Demand spike (velocity >= 1.5x)'],
] as const

function App() {
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [reviewLoadFailedId, setReviewLoadFailedId] = useState<string | null>(null)
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const eventCursor = useRef(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [rejectOpen, setRejectOpen] = useState(false)
  const [approveOpen, setApproveOpen] = useState(false)
  const [customLabOpen, setCustomLabOpen] = useState(false)
  const [rejectComment, setRejectComment] = useState('')
  const [notice, setNotice] = useState('')
  const [scenario, setScenario] = useState<DemoScenario | null>(null)

  const loadRecommendations = useCallback(async () => {
    try {
      const result = await api.recommendations()
      setRecommendations(result.items)
      if (!selected && result.items.length) setSelected(result.items[0].review_id ?? `rec:${result.items[0].id}`)
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load recommendations.') }
  }, [selected])

  const recommendationId = selected?.startsWith('rec:') ? selected.slice(4) : null
  const reviewId = selected && !selected.startsWith('rec:') ? selected : null

  const loadReview = useCallback(async () => {
    if (!reviewId) { setReview(null); setEvents([]); eventCursor.current = 0; return null }
    setReviewLoadFailedId(null)
    try {
      const [nextReview, nextEvents] = await Promise.all([api.review(reviewId), api.events(reviewId, eventCursor.current)])
      setReview(nextReview)
      if (nextEvents.items.length) {
        setEvents(current => {
          const knownIds = new Set(current.map(event => event.id))
          const additions = nextEvents.items.filter(event => !knownIds.has(event.id))
          return [...current, ...additions]
        })
        eventCursor.current = Math.max(eventCursor.current, nextEvents.items[nextEvents.items.length - 1].id)
      }
      return nextReview
    } catch (e) {
      setReviewLoadFailedId(reviewId)
      setError(e instanceof Error ? e.message : 'Unable to load review.')
      return null
    }
  }, [reviewId])

  useEffect(() => { void loadRecommendations() }, [loadRecommendations])
  useEffect(() => {
    eventCursor.current = 0
    setEvents([])
  }, [reviewId])
  useEffect(() => {
    if (!reviewId || reviewLoadFailedId === reviewId) return
    let stopped = false
    let timer: number | undefined
    const poll = async () => {
      const latest = await loadReview()
      const waitingForBuyer = latest?.status === 'AWAITING_APPROVAL' || latest?.status === 'AWAITING_EXECUTION_RETRY'
      if (!stopped && latest && !waitingForBuyer && !isTerminal(latest.status)) {
        timer = window.setTimeout(poll, 2500)
      }
    }
    void poll()
    return () => {
      stopped = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [loadReview, reviewId, review?.status, reviewLoadFailedId])

  const selectedRecommendation = useMemo(() => {
    if (review && review.id === reviewId) return review.recommendation
    const id = recommendationId
    return recommendations.find(item => item.id === id) ?? null
  }, [recommendationId, recommendations, review])

  async function startReview(targetRecommendationId = recommendationId) {
    if (!targetRecommendationId) return
    setBusy(true); setError('')
    try {
      const result = await api.startReview(targetRecommendationId)
      setSelected(result.review_id)
      setNotice('Review started. Gathering evidence now.')
      window.setTimeout(() => setNotice(''), 3500)
      await loadRecommendations()
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not start review.') }
    finally { setBusy(false) }
  }

  async function submitApproval() {
    if (!review?.decision) return
    setBusy(true); setError('')
    try {
      const result = await api.approve(review.id, review.decision.version)
      setApproveOpen(false)
      setNotice(result.duplicate
        ? `This approval was already processed. Current review status: ${result.status.replaceAll('_', ' ').toLowerCase()}. Check the latest evidence before taking another action.`
        : 'Approval recorded. Revalidating facts before execution.')
      await loadReview()
    } catch (e) { setApproveOpen(false); await loadReview(); setError(e instanceof Error ? e.message : 'Approval failed.') }
    finally { setBusy(false) }
  }

  async function rejectProposal() {
    if (!review?.decision || !rejectComment.trim()) return
    setBusy(true); setError('')
    try {
      await api.reject(review.id, review.decision.version, rejectComment)
      setRejectOpen(false); setRejectComment(''); await loadReview()
    } catch (e) { setError(e instanceof Error ? e.message : 'Rejection failed.') }
    finally { setBusy(false) }
  }

  async function retryExecution() {
    if (!review?.decision) return
    setBusy(true); setError('')
    try {
      await api.retryExecution(review.id, review.decision.version)
      setNotice('Execution retry initiated. Refreshing facts.')
      await loadReview()
    } catch (e) { setError(e instanceof Error ? e.message : 'Execution retry failed.') }
    finally { setBusy(false) }
  }

  async function simulatePartial() {
    setBusy(true); setError('')
    try {
      const fixture = await api.partialFixture()
      const result = await api.confirmPartial({ purchase_order_id: fixture.purchase_order_id, product_id: fixture.product_id, confirmed_quantity: 250 })
      setSelected(result.review_id)
      setNotice('Supplier confirmed 250 of 500 units. Recomputing coverage and options.')
      await loadRecommendations()
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not submit supplier confirmation.') }
    finally { setBusy(false) }
  }

  async function resetScenario(name: string) {
    setBusy(true); setError('')
    try {
      const result = await api.resetScenario(name)
      setScenario(result)
      setSelected(result.review_id)
      setReview(null); setEvents([])
      setNotice(`${result.title} is ready. ${result.next_step}`)
      await loadRecommendations()
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not prepare the scenario.') }
    finally { setBusy(false) }
  }

  async function advanceScenario() {
    if (!scenario || !review) return
    setBusy(true); setError('')
    try {
      const result = await api.advanceScenario(scenario.scenario, review.id)
      setScenario(current => current ? { ...current, next_step: result.next_step, requires_advance: false } : current)
      setNotice('Supplier terms changed. Approve the original proposal to see the safety check supersede it.')
      await loadReview()
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not apply the supplier update.') }
    finally { setBusy(false) }
  }

  async function submitCustomScenario(data: Record<string, unknown>) {
    setBusy(true); setError('')
    try {
      const res = await api.createCustomScenario(data)
      setCustomLabOpen(false)
      setSelected(res.review_id)
      setNotice(`Custom scenario "${data.name}" created and launched!`)
      await loadRecommendations()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create custom scenario.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><Boxes size={17} /></span><span>stockwise<span className="brand-dot">.</span></span></div>
        <div className="workspace-switch"><div className="workspace-icon">N</div><div><small>WORKSPACE</small><strong>Northstar Retail</strong></div><ChevronDown size={15} /></div>
        <div className="nav-label">WORKSPACE</div>
        <button className="nav-item active"><LayoutDashboard size={17} />Overview</button>
        <button className="nav-item"><PackageSearch size={17} />Recommendations <span className="nav-count">{recommendations.length}</span></button>
        <button className="nav-item"><Truck size={17} />Purchase orders</button>
        <button className="nav-item"><Warehouse size={17} />Locations</button>
        <div className="sidebar-bottom"><div className="sidebar-status"><span className="pulse" />All systems operational</div><div className="user-row"><div className="avatar">AC</div><div><strong>Aditya Chawla</strong><small>Buyer · Bengaluru</small></div><ChevronDown size={15} /></div></div>
      </aside>

      <main className="main-area">
        <header className="topbar"><div className="topbar-left"><div className="top-brand"><span className="top-brand-mark"><Boxes size={18} /></span>stockwise<span>.</span></div><nav aria-label="Primary navigation"><button className="top-nav active">My desk</button><button className="top-nav">Suppliers</button><button className="top-nav">Purchase orders</button></nav></div><div className="top-actions"><span className="today"><Clock3 size={14} />{new Date().toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}</span><button className="icon-button" aria-label="Refresh data" onClick={() => { void loadRecommendations(); void loadReview() }}><RefreshCw size={16} /></button><button className="help-button"><CircleHelp size={16} />Help</button></div></header>
        <div className="intelligence-ticker" aria-label="Purchasing intelligence status"><span>Evidence-led purchasing decisions</span><i /> <span>Human approval before every purchase order</span><i /> <span>Read-back validation after execution</span></div>

        <div className="content-wrap">
          <section className="page-heading shop-hero"><div className="hero-copy"><div className="location-chip"><MapPin size={15} />Bengaluru Fulfilment Centre <ChevronDown size={14} /></div><h1>Purchasing intelligence<br />for <em>every decision.</em></h1><p>Stockwise investigates demand, supply, budget, and storage before a buyer approves an order.</p><div className="hero-actions"><button className="hero-primary" onClick={() => { if (recommendationId) void startReview() }} disabled={busy || !recommendationId}>{busy ? <LoaderCircle className="spin" size={16} /> : <PackageSearch size={16} />}{busy ? 'Checking recommendation' : recommendationId ? 'Review selected item' : 'Choose an item to review'}</button><button className="hero-secondary" onClick={simulatePartial} disabled={busy}><Activity size={16} />See supplier exception</button></div></div><div className="hero-delivery planning-scene" aria-hidden="true"><span className="hero-sticker one">GUARDED WORKFLOW</span><span className="hero-sticker two">8 checks</span><div className="hero-bag"><Boxes size={49} /><b>350</b><small>SAFE UNITS</small></div><span className="hero-route" /></div></section>

          <section className="metric-grid" aria-label="Purchasing overview">
            <Metric label="Needs your review" value={recommendations.filter(x => x.review_status === 'AWAITING_APPROVAL' || !x.review_status).length.toString().padStart(2, '0')} detail="Across all locations" icon={<FileText size={17} />} tone="violet" trend="Action needed" />
            <Metric label="Decisions today" value={recommendations.filter(x => x.review_status).length.toString().padStart(2, '0')} detail="Recommendations evaluated" icon={<CheckCircle2 size={17} />} tone="green" trend="Live" />
            <Metric label="Purchase orders" value={review?.action || (review?.actions && review.actions.length > 0) ? String(review.actions?.length || 1).padStart(2, '0') : '—'} detail="Created and validated" icon={<PackageCheck size={17} />} tone="blue" trend={review?.validation?.status ?? 'Awaiting action'} />
            <Metric label="Policy checks" value={review?.decision?.constraints.filter(x => x.passed).length?.toString().padStart(2, '0') ?? '—'} detail="Hard constraints passing" icon={<ShieldCheck size={17} />} tone="amber" trend={review?.decision ? 'Deterministic' : 'Ready'} />
          </section>

          {error && <div className="alert error-alert"><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error"><X size={15} /></button></div>}
          {notice && <div className="alert notice-alert"><Check size={16} />{notice}</div>}

          <section className="scenario-lab" aria-labelledby="scenario-lab-title">
            <div className="scenario-lab-title"><span className="scenario-lab-icon"><FlaskConical size={16} /></span><div><h2 id="scenario-lab-title">Scenario Lab</h2><p>Resettable proof points for safety controls and multi-supplier allocation.</p></div>{scenario && <button className="expand-toggle scenario-reset-button" onClick={() => void resetScenario(scenario.scenario)} disabled={busy} aria-label={`Reset ${scenario.title} testcase`}>{busy ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}Reset this test case</button>}</div>
            <div className="scenario-options" aria-label="Choose a demo scenario">
              {scenarioChoices.map(([id, label]) => <button key={id} className={scenario?.scenario === id ? 'selected' : ''} aria-pressed={scenario?.scenario === id} onClick={() => void resetScenario(id)} disabled={busy}>{label}</button>)}
              <button className="custom-lab-button" onClick={() => setCustomLabOpen(true)} disabled={busy}><Sparkles size={14} /> Custom Scenario Lab</button>
            </div>
            {scenario && <ScenarioGuide scenario={scenario} review={review} busy={busy} onAdvance={advanceScenario} />}
          </section>

          <div className="work-grid">
            <section className="panel queue-panel">
              <div className="panel-heading"><div><div className="section-kicker">PURCHASING QUEUE</div><h2>Recommendations <span className="subtle-count">{recommendations.length}</span></h2></div><button className="text-button" onClick={() => { void loadRecommendations(); void loadReview() }}>View all <ArrowRight size={14} /></button></div>
              <div className="queue-list">
                {recommendations.map((item, index) => <RecommendationRow key={item.id} item={item} active={(item.review_id && selected === item.review_id) || selected === `rec:${item.id}`} index={index} onClick={() => { setReview(null); setEvents([]); setSelected(item.review_id ?? `rec:${item.id}`) }} />)}
                {!recommendations.length && <div className="empty-state">No recommendations found.</div>}
              </div>
              <div className="queue-foot"><span><span className="live-dot" />Seeded evaluation scenarios</span><button className="icon-button small" aria-label="Refresh recommendations" onClick={() => void loadRecommendations()}><RefreshCw size={14} /></button></div>
            </section>

            <section className="panel review-panel">
              {!selectedRecommendation ? <div className="empty-review"><PackageSearch size={28} /><h3>Select a recommendation</h3><p>Choose an item from the queue to inspect its purchasing situation.</p></div> : !review ? <RecommendationDetail item={selectedRecommendation} busy={busy} onStart={() => startReview()} /> : <ReviewDetail review={review} events={events} busy={busy} onApprove={() => setApproveOpen(true)} onReject={() => setRejectOpen(true)} onRetry={retryExecution} onRunAgain={() => startReview(review.recommendation.id)} />}
            </section>
          </div>

          <footer className="page-footer"><span>Decision support powered by deterministic policy checks</span><span>DEMO ENVIRONMENT <i /></span></footer>
        </div>
      </main>

      {rejectOpen && <div className="modal-backdrop" role="presentation"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="reject-title"><button className="modal-close" onClick={() => setRejectOpen(false)} aria-label="Close"><X size={18} /></button><span className="modal-icon"><AlertTriangle size={19} /></span><h2 id="reject-title">Reject this proposal?</h2><p>The purchase order will not be created. Add a short reason for the audit trail.</p><label htmlFor="reject-reason">Reason</label><textarea id="reject-reason" value={rejectComment} onChange={e => setRejectComment(e.target.value)} placeholder="Why are you rejecting this recommendation?" rows={3} /><div className="modal-actions"><button className="secondary-button" onClick={() => setRejectOpen(false)}>Keep reviewing</button><button className="danger-button" disabled={!rejectComment.trim() || busy} onClick={rejectProposal}>{busy ? 'Submitting…' : 'Reject proposal'}</button></div></section></div>}
      {approveOpen && review?.decision && <div className="modal-backdrop" role="presentation"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="approve-title"><button className="modal-close" onClick={() => setApproveOpen(false)} aria-label="Close"><X size={18} /></button><span className="modal-icon"><ShieldCheck size={19} /></span><h2 id="approve-title">Confirm purchase order</h2><p>Approval is for this exact proposal. The system will refresh facts before creating the order.</p><div className="approval-confirmation"><span>{review.recommendation.supplier.name}</span><strong>{review.recommendation.product.sku} · {review.recommendation.node.code}</strong><b>{review.decision.proposed_quantity.toLocaleString()} units · {money(Number(review.decision.calculations.total_cost_minor ?? 0), String(review.decision.calculations.currency ?? 'INR'))}</b><small>Proposal version {review.decision.version}</small></div><div className="modal-actions"><button className="secondary-button" onClick={() => setApproveOpen(false)}>Keep reviewing</button><button className="primary-button" disabled={busy} onClick={submitApproval}>{busy ? 'Submitting…' : 'Approve exact proposal'}</button></div></section></div>}
      <CustomScenarioModal open={customLabOpen} onClose={() => setCustomLabOpen(false)} onSubmit={submitCustomScenario} busy={busy} />
    </div>
  )
}

function ScenarioGuide({ scenario, review, busy, onAdvance }: { scenario: DemoScenario; review: Review | null; busy: boolean; onAdvance: () => void }) {
  const guarded = scenario.scenario === 'inventory-conflict' ? review?.status === 'NEEDS_ATTENTION'
    : scenario.scenario === 'late-incoming-po' ? Number(review?.decision?.calculations.raw_need) === 600
    : scenario.scenario === 'supplier-terms-change' ? (review?.decision?.version ?? 0) > 1
    : scenario.scenario === 'lost-create-response' ? review?.validation?.status === 'PASSED'
    : scenario.scenario === 'multi-supplier-shortfall' ? (review?.sourcing_plan?.lines.length ?? 0) >= 2
    : scenario.scenario === 'demand-spike' ? (review?.decision?.proposed_quantity ?? 0) > 0
    : review?.validation?.status === 'FAILED_UNSAFE'
  return <div className="scenario-guide">
    <div className="scenario-copy"><span className={`scenario-state ${guarded ? 'passed' : ''}`}>{guarded ? 'Guard confirmed' : 'Test in progress'}</span><strong>{scenario.title}</strong><p>{scenario.purpose}</p><small>Expected: {scenario.expected_outcome}</small></div>
    <div className="scenario-next"><span>Next step</span><p>{scenario.next_step}</p>{scenario.requires_advance && <button className="secondary-button" disabled={busy || review?.status !== 'AWAITING_APPROVAL'} onClick={onAdvance}>{busy ? 'Applying update…' : 'Apply supplier update'}</button>}</div>
  </div>
}

function Metric({ label, value, detail, icon, tone, trend }: { label: string; value: string; detail: string; icon: React.ReactNode; tone: string; trend: string }) {
  return <article className="metric-card"><div className={`metric-icon ${tone}`}>{icon}</div><div className="metric-top"><span>{label}</span><span className={`metric-trend ${tone}`}>{trend}</span></div><div className="metric-value">{value}</div><div className="metric-detail">{detail}</div></article>
}

function RecommendationRow({ item, active, index, onClick }: { item: Recommendation; active: boolean; index: number; onClick: () => void }) {
  const status = item.review_status ?? 'NEW'
  const colors = ['mint', 'lavender', 'peach', 'sky']
  return <button className={`queue-row ${active ? 'selected' : ''}`} onClick={onClick}>
    <span className={`product-thumb ${colors[index % colors.length]}`}><span>{item.product.name.slice(0, 1)}</span><i /></span>
    <span className="queue-item-main"><strong>{item.product.name}</strong><small>{item.product.sku} <i /> {item.node.code} <i /> {item.supplier.name}</small></span>
    <span className="queue-quantity"><strong>{item.recommended_quantity.toLocaleString()}</strong><small>units</small></span>
    <span className={`status-pill ${status.toLowerCase().replaceAll('_', '-')}`}>{status === 'NEW' ? 'New' : status.replaceAll('_', ' ').toLowerCase()}</span>
    <ArrowRight className="row-arrow" size={15} />
  </button>
}

function RecommendationDetail({ item, busy, onStart }: { item: Recommendation; busy: boolean; onStart: () => void }) {
  return <>
    <div className="detail-header"><div className="detail-ident"><span className="product-thumb large mint"><span>{item.product.name.slice(0, 1)}</span><i /></span><div><div className="section-kicker">PURCHASE RECOMMENDATION</div><h2>{item.product.name}</h2><p>{item.product.sku} <i /> {item.node.name}</p></div></div><span className="status-pill new">New recommendation</span></div>
    <div className="source-strip"><Sparkles size={15} /><span>Source recommendation from purchasing planner</span><span className="source-id">{item.source_reference}</span></div>
    <div className="quantity-hero"><div><span className="data-label">RECOMMENDED ORDER</span><div className="quantity-number">{item.recommended_quantity.toLocaleString()} <small>units</small></div><div className="quantity-caption">From <b>{item.supplier.name}</b> · {item.node.name}</div></div><div className="quantity-decor"><Boxes size={32} /></div></div>
    <div className="review-prompt"><div className="prompt-icon"><PackageSearch size={17} /></div><div><strong>Recommendation is a starting point</strong><p>Check current stock, incoming orders, demand and purchasing constraints before deciding.</p></div></div>
    <div className="detail-footer"><span><ShieldCheck size={15} />No purchase order is created without your approval.</span><button className="primary-button" disabled={busy} onClick={onStart}>{busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}{busy ? 'Starting review' : 'Investigate recommendation'}<ArrowRight size={15} /></button></div>
  </>
}

function ActionsList({ actions }: { actions: ActionItem[] }) {
  if (!actions || actions.length <= 1) return null
  return (
    <div className="section-block actions-block">
      <div className="section-title">
        <div>
          <h3>Executed purchase orders</h3>
          <p>{actions.length} supplier orders created and validated</p>
        </div>
        <span className="policy-tag"><PackageCheck size={13} />MULTI-PO</span>
      </div>
      <div className="actions-table-wrap">
        <table className="actions-table">
          <thead>
            <tr>
              <th>Attempt</th>
              <th>External ID</th>
              <th>Status</th>
              <th>Amount</th>
              <th>Validation</th>
              <th>Idempotency Key</th>
            </tr>
          </thead>
          <tbody>
            {actions.map((act) => (
              <tr key={`${act.idempotency_key}:${act.attempt_number}`}>
                <td>{act.attempt_number}</td>
                <td><strong>{act.external_id ?? act.purchase_order_id ?? '—'}</strong></td>
                <td><span className={`status-pill ${act.status.toLowerCase()}`}>{act.status}</span></td>
                <td>{act.total_minor !== null && act.currency ? money(act.total_minor, act.currency) : 'Unavailable'}</td>
                <td><span className={`status-pill ${(act.validation_status ?? 'pending').toLowerCase()}`}>{act.validation_status ?? 'Pending'}</span>{act.mismatch_codes?.length ? <small>{act.mismatch_codes.join(', ')}</small> : null}</td>
                <td className="font-mono text-muted">{act.idempotency_key.slice(-16)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ReviewDetail({
  review,
  events,
  busy,
  onApprove,
  onReject,
  onRetry,
  onRunAgain,
}: {
  review: Review
  events: TimelineEvent[]
  busy: boolean
  onApprove: () => void
  onReject: () => void
  onRetry: () => void
  onRunAgain: () => void
}) {
  const decision = review.decision
  const statusLabel = review.status.replaceAll('_', ' ').toLowerCase()
  const calc = decision?.calculations ?? {}
  const explanationProvider = decision?.explanation_source?.provider ?? 'template'
  const explanationLabel = explanationProvider === 'template' ? 'Deterministic explanation' : `${humanize(explanationProvider)} explanation`

  return <>
    <div className="detail-header"><div className="detail-ident"><span className="product-thumb large mint"><span>{review.recommendation.product.name.slice(0, 1)}</span><i /></span><div><div className="section-kicker">PURCHASE REVIEW <span className="review-ref">#{review.id.slice(0, 8).toUpperCase()}</span></div><h2>{review.recommendation.product.name}</h2><p>{review.recommendation.product.sku} <i /> {review.recommendation.node.name}</p></div></div><div className="detail-actions"><span className={`status-pill ${statusLabel.replaceAll(' ', '-')}`}>{statusLabel}</span>{isTerminal(review.status) && ['REC-800', 'REC-ACCEPT', 'REC-REJECT'].includes(review.recommendation.source_reference) && <button className="expand-toggle" onClick={onRunAgain} disabled={busy}>Run again with fresh facts</button>}</div></div>

    {review.status === 'AWAITING_EXECUTION_RETRY' && <ExecutionRetryBanner proposalVersion={review.decision?.version} busy={busy} onRetry={onRetry} />}

    {!decision && <div className="progress-card"><span className="progress-orb"><LoaderCircle className="spin" size={21} /></span><div><strong>Investigating the purchasing situation</strong><p>Collecting inventory, demand, open orders, supplier terms, budget and storage capacity.</p></div></div>}
    {review.investigation && !decision && <ToolTraceDrawer trace={review.investigation} />}
    {decision && <>
      <div className={`decision-banner ${decision.type.toLowerCase()}`}><div className="decision-symbol">{decision.type === 'INVESTIGATE' ? <AlertTriangle size={19} /> : decision.type === 'REJECT' ? <X size={19} /> : <Check size={19} />}</div><div className="decision-copy"><span className="section-kicker">DETERMINISTIC DECISION <span className="confidence">{decision.confidence} CONFIDENCE</span></span><h3>{decision.type === 'MODIFY' ? 'Adjust the recommended quantity' : decision.type === 'ACCEPT' ? 'Recommendation is supported' : decision.type === 'REJECT' ? 'No additional purchase needed' : 'More information is needed'}</h3><p><span className="ai-label">{explanationLabel}</span> {decision.explanation.summary}</p>{(decision.unresolved_quantity ?? 0) > 0 && <p><strong>{decision.unresolved_quantity?.toLocaleString()} units remain unresolved</strong> after the safe purchasing limits are applied.</p>}</div><div className="decision-qty"><small>PROPOSED</small><strong>{decision.proposed_quantity.toLocaleString()}</strong><span>units</span></div></div>

      {!!decision.explanation.important_factors.length && <div className="factor-list" aria-label="Decision reasons">{decision.explanation.important_factors.map(factor => <span key={factor.reason_code}>{humanize(factor.reason_code)}{factor.evidence_refs.length ? ` · ${factor.evidence_refs.map(humanize).join(', ')}` : ''}</span>)}</div>}

      <DecisionMath review={review} />

      <div className="section-block"><div className="section-title"><div><h3>Decision factors</h3><p>Calculated from current operational evidence</p></div><span className="policy-tag"><ShieldCheck size={13} />POLICY CHECKED</span></div>
        <div className="calculation-grid">
          <Calc label="Usable on hand" value={num(calc.usable_on_hand)} hint={`On hand ${num(valueAt(review.evidence.items, 'inventory', 'on_hand'))} less reserved & damaged`} />
          <Calc label="Confirmed incoming" value={num(valueAt(review.evidence.items, 'open_purchase_orders', 'value'))} hint="Supplier-confirmed open POs" />
          <Calc label="Target stock" value={num(calc.target_stock)} hint="Forecast demand + safety stock" />
          <Calc label="Net requirement" value={num(calc.raw_need)} hint="Target less inventory position" highlight />
        </div>
      </div>

      {review.investigation && <ToolTraceDrawer trace={review.investigation} />}

      <div className="section-block evidence-block"><div className="section-title"><div><h3>Evidence checked</h3><p>{review.evidence.completeness === 'COMPLETE' ? 'All required sources reviewed' : 'Some evidence needs attention'}</p></div><span className={`evidence-count ${review.evidence.completeness === 'COMPLETE' ? '' : 'warning'}`}>{review.evidence.items.length} sources</span></div>
        <div className="evidence-list">{review.evidence.items.map((fact: Evidence, i: number) => <EvidenceRow key={fact.name} fact={fact} index={i} />)}</div>
        {!!review.evidence.errors.length && <div className="evidence-warning"><AlertTriangle size={15} />{review.evidence.errors.join(' · ')}</div>}
      </div>

      <div className="section-block constraints-block"><div className="section-title"><div><h3>Constraint checks</h3><p>Every hard limit is validated before execution</p></div><span className="check-summary">{decision.constraints.filter(x => x.passed).length}/{decision.constraints.length} pass</span></div><div className="constraint-list">{decision.constraints.map(check => <div className="constraint-row" key={check.code}><span className={`constraint-icon ${check.passed ? 'pass' : 'fail'}`}>{check.passed ? <Check size={12} /> : <X size={12} />}</span><span>{humanize(check.code)}</span><small>{constraintDetail(check)}</small></div>)}</div></div>

      {review.sourcing_plan && <SourcingPlanView plan={review.sourcing_plan} recovery={review.scenario_type === 'supplier-shortfall' || review.scenario_type === 'demand-change'} />}

      {review.status === 'AWAITING_APPROVAL' && <div className="approval-bar"><div><strong>Ready for your decision</strong><span>Proposal v{decision.version} · {money(Number(decision.calculations.total_cost_minor ?? 0), String(decision.calculations.currency ?? 'INR'))} total</span></div><div className="approval-actions"><button className="reject-button" onClick={onReject} disabled={busy}>Reject</button><button className="primary-button" onClick={onApprove} disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}Approve & create PO</button></div></div>}

      {review.status === 'EXECUTING' || review.status === 'VALIDATING' ? <div className="progress-card compact"><span className="progress-orb"><LoaderCircle className="spin" size={19} /></span><div><strong>{review.status === 'EXECUTING' ? 'Creating purchase order' : 'Validating the purchase order'}</strong><p>Verifying the persisted result against the approved proposal.</p></div></div> : null}

      {review.actions && review.actions.length > 1 ? (
        <ActionsList actions={review.actions} />
      ) : review.action ? (
        <div className={`result-card ${review.validation?.status === 'PASSED' ? 'success' : ''}`}><div className="result-icon">{review.validation?.status === 'PASSED' ? <CheckCircle2 size={18} /> : <FileCheck2 size={18} />}</div><div><strong>{review.validation?.status === 'PASSED' ? 'Purchase order created and validated' : 'Purchase order action recorded'}</strong><p>{review.action.external_id} · {review.action.total_minor !== null && review.action.currency ? money(review.action.total_minor, review.action.currency) : 'Unavailable'}</p></div><span className="validation-pill">{review.validation?.status ?? 'VALIDATING'}</span></div>
      ) : null}

      {review.status === 'NEEDS_ATTENTION' && <div className="needs-attention"><AlertTriangle size={17} /><div><strong>Buyer attention needed</strong><p>{review.evidence.errors.join(' · ') || decision.reason_codes.map(humanize).join(' · ')}</p></div></div>}
      {review.status === 'REJECTED_BY_BUYER' && <div className="result-card muted"><X size={17} /><div><strong>Proposal rejected</strong><p>{review.approval?.comment || 'No purchase order was created.'}</p></div></div>}
      {review.status === 'COMPLETED' && !review.action && (!review.actions || review.actions.length === 0) && <div className="result-card success"><CheckCircle2 size={18} /><div><strong>Review complete — no purchase order created</strong><p>The recommendation was rejected because no additional inventory is required.</p></div></div>}
    </>}

    {review.status === 'NEEDS_ATTENTION' && !decision && <div className="needs-attention"><AlertTriangle size={17} /><div><strong>Recovery limit reached</strong><p>Partial fulfilment could not be safely replanned after {review.recovery_attempts ?? 0} recovery attempts. No new purchase order was created.</p></div></div>}
    {review.status === 'FAILED' && <div className="needs-attention"><AlertTriangle size={17} /><div><strong>Review could not be completed</strong><p>Refresh the review and check the activity timeline before retrying.</p></div></div>}

    <div className="timeline"><div className="section-title"><div><h3>Activity timeline</h3><p>Auditable steps in this review</p></div><span className="timeline-count">{events.length} events</span></div>{events.length ? <div className="timeline-list">{events.slice(-10).reverse().map((event, idx) => { const copy = timelineCopy(event); return <div className="timeline-row" key={`${event.id}-${event.event_type}-${idx}`}><span className="timeline-icon"><TimelineIcon type={event.event_type} /></span><div><strong>{copy.title}</strong><small>{new Date(event.created_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}{copy.detail ? ` · ${copy.detail}` : ''}</small></div></div> })}</div> : <div className="timeline-empty">Activity will appear here as the review progresses.</div>}</div>

  </>
}

export function LegacyCustomScenarioModal({
  open,
  onClose,
  onSubmit,
  busy,
}: {
  open: boolean
  onClose: () => void
  onSubmit: (data: Record<string, unknown>) => Promise<void>
  busy: boolean
}) {
  const [name, setName] = useState('Evaluator Custom Scenario')
  const [mode, setMode] = useState<'RECOMMENDATION' | 'DEMAND_CHANGE'>('RECOMMENDATION')
  const [sku, setSku] = useState('CUSTOM-01')
  const [productName, setProductName] = useState('Custom Operational Item')
  const [unitVolume, setUnitVolume] = useState(2)
  const [nodeCode, setNodeCode] = useState('BLR-01')
  const [nodeName, setNodeName] = useState('Bengaluru FC')
  const [recommendedQty, setRecommendedQty] = useState(800)
  const [onHand, setOnHand] = useState(100)
  const [reserved, setReserved] = useState(0)
  const [damaged, setDamaged] = useState(0)
  const [baselineForecast, setBaselineForecast] = useState(500)
  const [revisedForecast, setRevisedForecast] = useState(900)
  const [supp1Code, setSupp1Code] = useState('SUP-01')
  const [supp1Name, setSupp1Name] = useState('Primary Vendor')
  const [supp1Cost, setSupp1Cost] = useState(1200)
  const [supp1Rel, setSupp1Rel] = useState(9700)
  const [supp1Max, setSupp1Max] = useState(300)
  const [supp2Code, setSupp2Code] = useState('SUP-02')
  const [supp2Name, setSupp2Name] = useState('Alternate Vendor')
  const [supp2Cost, setSupp2Cost] = useState(1350)
  const [supp2Rel, setSupp2Rel] = useState(9200)
  const [supp2Max, setSupp2Max] = useState(500)
  const [budgetAvailable, setBudgetAvailable] = useState(1000000)
  const [storageVolume, setStorageVolume] = useState(5000)
  const [safetyStock, setSafetyStock] = useState(50)

  if (!open) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const now = new Date()
    const payload = {
      name,
      mode,
      product: { sku, name: productName, unit_volume: Number(unitVolume) },
      node: { code: nodeCode, name: nodeName },
      recommended_quantity: Number(recommendedQty),
      inventory: { on_hand: Number(onHand), reserved: Number(reserved), damaged: Number(damaged) },
      forecasts: {
        baseline: Number(baselineForecast),
        revised: mode === 'DEMAND_CHANGE' ? Number(revisedForecast) : null,
        window_start: now.toISOString(),
        window_end: new Date(now.getTime() + 7 * 24 * 3600 * 1000).toISOString(),
      },
      suppliers: [
        {
          code: supp1Code,
          name: supp1Name,
          reliability_score_bps: Number(supp1Rel),
          unit_cost_minor: Number(supp1Cost),
          currency: 'INR',
          minimum_order_quantity: 25,
          lead_time_days: 2,
          max_available_quantity: Number(supp1Max),
        },
        {
          code: supp2Code,
          name: supp2Name,
          reliability_score_bps: Number(supp2Rel),
          unit_cost_minor: Number(supp2Cost),
          currency: 'INR',
          minimum_order_quantity: 25,
          lead_time_days: 3,
          max_available_quantity: Number(supp2Max),
        },
      ],
      budget: { available_minor: Number(budgetAvailable), currency: 'INR' },
      storage: { available_volume: Number(storageVolume) },
      policy: { safety_stock: Number(safetyStock), review_period_days: 2 },
      recent_sales: mode === 'DEMAND_CHANGE' ? { units_sold: 280, window_hours: 24 } : null,
    }
    await onSubmit(payload)
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal custom-lab-modal" role="dialog" aria-modal="true" aria-labelledby="custom-lab-title">
        <button className="modal-close" onClick={onClose} aria-label="Close"><X size={18} /></button>
        <span className="modal-icon"><FlaskConical size={19} /></span>
        <h2 id="custom-lab-title">Custom Scenario Lab</h2>
        <p>Define operational constraints and test multi-supplier or demand-spike intelligence live without writing code.</p>
        <form onSubmit={handleSubmit}>
          <div className="custom-form-grid">
            <div className="form-field form-field-full">
              <label>Scenario Name</label>
              <input value={name} onChange={e => setName(e.target.value)} required />
            </div>
            <div className="form-field form-field-full">
              <label>Evaluation Mode</label>
              <select value={mode} onChange={e => setMode(e.target.value as 'RECOMMENDATION' | 'DEMAND_CHANGE')}>
                <option value="RECOMMENDATION">Standard / Multi-Supplier Recommendation</option>
                <option value="DEMAND_CHANGE">Demand Spike Change (24h sales jump)</option>
              </select>
            </div>

            <div className="form-section-title">Product & Node</div>
            <div className="form-field">
              <label>SKU</label>
              <input value={sku} onChange={e => setSku(e.target.value)} required />
            </div>
            <div className="form-field">
              <label>Product Name</label>
              <input value={productName} onChange={e => setProductName(e.target.value)} required />
            </div>
            <div className="form-field">
              <label>Node Code</label>
              <input value={nodeCode} onChange={e => setNodeCode(e.target.value)} required />
            </div>
            <div className="form-field">
              <label>Node Name</label>
              <input value={nodeName} onChange={e => setNodeName(e.target.value)} required />
            </div>
            <div className="form-field">
              <label>Unit Volume (m³)</label>
              <input type="number" min={1} value={unitVolume} onChange={e => setUnitVolume(Number(e.target.value))} required />
            </div>
            <div className="form-field">
              <label>Recommended Qty</label>
              <input type="number" min={0} value={recommendedQty} onChange={e => setRecommendedQty(Number(e.target.value))} required />
            </div>

            <div className="form-section-title">Inventory & Forecast</div>
            <div className="form-field">
              <label>Usable On-Hand</label>
              <input type="number" min={0} value={onHand} onChange={e => setOnHand(Number(e.target.value))} required />
            </div>
            <div className="form-field">
              <label>Reserved Units</label>
              <input type="number" min={0} value={reserved} onChange={e => setReserved(Number(e.target.value))} required />
            </div>
            <div className="form-field">
              <label>Damaged Units</label>
              <input type="number" min={0} value={damaged} onChange={e => setDamaged(Number(e.target.value))} required />
            </div>
            <div className="form-field">
              <label>Baseline Forecast</label>
              <input type="number" min={0} value={baselineForecast} onChange={e => setBaselineForecast(Number(e.target.value))} required />
            </div>
            {mode === 'DEMAND_CHANGE' && (
              <div className="form-field form-field-full">
                <label>Revised Forecast (Spike)</label>
                <input type="number" min={0} value={revisedForecast} onChange={e => setRevisedForecast(Number(e.target.value))} required />
              </div>
            )}

            <div className="form-section-title">Supplier 1 (Primary)</div>
            <div className="form-field">
              <label>Code</label>
              <input value={supp1Code} onChange={e => setSupp1Code(e.target.value)} required />
            </div>
            <div className="form-field">
              <label>Name</label>
              <input value={supp1Name} onChange={e => setSupp1Name(e.target.value)} required />
            </div>
            <div className="form-field">
              <label>Unit Cost (Minor: ₹1 = 100)</label>
              <input type="number" min={1} value={supp1Cost} onChange={e => setSupp1Cost(Number(e.target.value))} required />
            </div>
            <div className="form-field">
              <label>Max Quantity</label>
              <input type="number" min={0} value={supp1Max} onChange={e => setSupp1Max(Number(e.target.value))} required />
            </div>
            <div className="form-field form-field-full">
              <label>Reliability BPS (0-10000)</label>
              <input type="number" min={0} max={10000} value={supp1Rel} onChange={e => setSupp1Rel(Number(e.target.value))} required />
            </div>

            <div className="form-section-title">Supplier 2 (Secondary / Alternate)</div>
            <div className="form-field">
              <label>Code</label>
              <input value={supp2Code} onChange={e => setSupp2Code(e.target.value)} required />
            </div>
            <div className="form-field">
              <label>Name</label>
              <input value={supp2Name} onChange={e => setSupp2Name(e.target.value)} required />
            </div>
            <div className="form-field">
              <label>Unit Cost (Minor: ₹1 = 100)</label>
              <input type="number" min={1} value={supp2Cost} onChange={e => setSupp2Cost(Number(e.target.value))} required />
            </div>
            <div className="form-field">
              <label>Max Quantity</label>
              <input type="number" min={0} value={supp2Max} onChange={e => setSupp2Max(Number(e.target.value))} required />
            </div>
            <div className="form-field form-field-full">
              <label>Reliability BPS (0-10000)</label>
              <input type="number" min={0} max={10000} value={supp2Rel} onChange={e => setSupp2Rel(Number(e.target.value))} required />
            </div>

            <div className="form-section-title">Global Physical Limits</div>
            <div className="form-field">
              <label>Available Budget (Minor: ₹1 = 100)</label>
              <input type="number" min={0} value={budgetAvailable} onChange={e => setBudgetAvailable(Number(e.target.value))} required />
            </div>
            <div className="form-field">
              <label>Available Storage Volume</label>
              <input type="number" min={0} value={storageVolume} onChange={e => setStorageVolume(Number(e.target.value))} required />
            </div>
            <div className="form-field form-field-full">
              <label>Safety Stock</label>
              <input type="number" min={0} value={safetyStock} onChange={e => setSafetyStock(Number(e.target.value))} required />
            </div>
          </div>
          <div className="modal-actions">
            <button type="button" className="secondary-button" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary-button" disabled={busy}>
              {busy ? 'Creating custom scenario…' : 'Run Custom Scenario'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

function Calc({ label, value, hint, highlight }: { label: string; value: string; hint: string; highlight?: boolean }) { return <div className={`calc-card ${highlight ? 'highlight' : ''}`}><span>{label}</span><strong>{value}</strong><small>{hint}</small></div> }

function DecisionMath({ review }: { review: Review }) {
  const decision = review.decision!
  const calc = decision.calculations
  const usable = Number(calc.usable_on_hand ?? 0)
  const incoming = Number(valueAt(review.evidence.items, 'open_purchase_orders', 'value'))
  const position = Number(calc.inventory_position ?? usable + incoming)
  const target = Number(calc.target_stock ?? 0)
  const requirement = Number(calc.raw_need ?? 0)
  const largest = Math.max(target, position, 1)
  const positionWidth = Math.min(100, (position / largest) * 100)
  const targetWidth = Math.min(100, (target / largest) * 100)
  return <section className="decision-math" aria-label="How the recommendation was calculated">
    <div className="section-title"><div><h3>How the quantity was calculated</h3><p>A visual audit trail of the policy calculation</p></div><span className="policy-tag">READ-ONLY MATH</span></div>
    <div className="math-equation">
      <MathCell label="Usable stock" value={usable} detail="On hand minus holds" />
      <span className="math-operator">+</span>
      <MathCell label="Confirmed incoming" value={incoming} detail="Open supplier POs" />
      <span className="math-operator">=</span>
      <MathCell label="Inventory position" value={position} detail="Stock available to plan" emphasis />
      <span className="math-operator">→</span>
      <MathCell label="Target stock" value={target} detail="Forecast plus safety stock" />
      <span className="math-operator">−</span>
      <MathCell label="Net requirement" value={requirement} detail="Before purchasing limits" emphasis />
    </div>
    <div className="coverage-chart" role="img" aria-label={`Inventory position ${position} units versus target stock ${target} units`}>
      <div className="chart-scale"><span>Current coverage</span><span>Target</span></div>
      <div className="chart-track"><span className="chart-position" style={{ width: `${positionWidth}%` }} /><i style={{ left: `${targetWidth}%` }} /></div>
      <div className="chart-caption"><span>{num(position)} units covered</span><strong>{num(requirement)} units still required</strong><span>{num(target)} unit target</span></div>
    </div>
  </section>
}

function MathCell({ label, value, detail, emphasis }: { label: string; value: number; detail: string; emphasis?: boolean }) { return <div className={`math-cell ${emphasis ? 'emphasis' : ''}`}><span>{label}</span><strong>{num(value)}</strong><small>{detail}</small></div> }

function EvidenceRow({ fact, index }: { fact: Evidence; index: number }) {
  const icons = [<Boxes size={15} />, <Activity size={15} />, <Truck size={15} />, <FileText size={15} />, <ShieldCheck size={15} />, <Warehouse size={15} />]
  const label = humanize(fact.name)
  const value = typeof fact.value === 'object' && fact.value !== null ? Object.entries(fact.value).map(([k, v]) => `${humanize(k)} ${v}`).join(' · ') : String(fact.value ?? 'No data')
  const detail = `${value} · ${fact.source} · observed ${new Date(fact.observed_at).toLocaleString('en-IN')}`
  return <div className="evidence-row"><span className={`evidence-icon ${fact.status.toLowerCase()}`}>{icons[index % icons.length]}</span><span className="evidence-name"><strong>{label}</strong><small>{detail}</small></span><span className={`evidence-state ${fact.status.toLowerCase()}`}>{fact.status === 'FRESH' ? <Check size={11} /> : <AlertTriangle size={11} />}{fact.status.toLowerCase()}</span></div>
}

function timelineCopy(event: TimelineEvent): { title: string; detail: string } {
  if (event.event_type === 'TOOL_CALL_COMPLETED') {
    const tool = typeof event.payload.tool === 'string' ? event.payload.tool : undefined
    const title = typeof event.payload.display_name === 'string' ? event.payload.display_name : toolAuditLabel(tool)
    const failed = event.payload.status === 'FAILED'
    const summary = typeof event.payload.summary === 'string' ? event.payload.summary : ''
    const errorCode = typeof event.payload.error_code === 'string' ? event.payload.error_code : undefined
    const duration = typeof event.payload.duration_ms === 'number' ? `${event.payload.duration_ms}ms` : ''
    return { title, detail: [failed ? readableError(errorCode) : summary || 'Evidence captured.', duration].filter(Boolean).join(' · ') }
  }
  if (event.event_type === 'INVESTIGATION_PROVIDER_FAILED') {
    const provider = typeof event.payload.provider === 'string' ? event.payload.provider : 'Provider'
    const failure = typeof event.payload.failure_code === 'string' ? event.payload.failure_code.replaceAll('_', ' ').toLowerCase() : 'unavailable'
    return { title: `${humanize(provider)} tool planning failed safely`, detail: `Fallback available · ${failure}` }
  }
  return { title: humanize(event.event_type), detail: event.payload.quantity ? `${event.payload.quantity} units` : '' }
}

function TimelineIcon({ type }: { type: string }) { if (type.includes('TOOL')) return <Activity size={13} />; if (type.includes('DECISION')) return <Sparkles size={13} />; if (type.includes('APPROVAL')) return <Check size={13} />; if (type.includes('VALIDATION')) return <FileCheck2 size={13} />; if (type.includes('ACTION')) return <PackageCheck size={13} />; if (type.includes('ESCALATED')) return <AlertTriangle size={13} />; return <Activity size={13} /> }
function num(value: unknown): string { return Number(value ?? 0).toLocaleString('en-IN') }
function valueAt(items: Evidence[], name: string, key: string): unknown { const fact = items.find(x => x.name === name); return typeof fact?.value === 'object' && fact.value !== null ? (fact.value as Record<string, unknown>)[key] : key === 'value' ? fact?.value : 0 }
function humanize(value: string): string { return value.toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase()) }
function money(value: number, currency: string): string { return new Intl.NumberFormat('en-IN', { style: 'currency', currency, maximumFractionDigits: 2 }).format(value / 100) }
function constraintDetail(check: { passed: boolean; observed?: unknown; limit?: unknown }): string {
  if (!check.passed) return 'Needs attention'
  if (check.observed !== undefined && check.limit !== undefined) return `${num(check.observed)} / ${num(check.limit)}`
  return 'Passed'
}
function isTerminal(status?: string): boolean { return ['COMPLETED', 'REJECTED_BY_BUYER', 'NEEDS_ATTENTION', 'FAILED'].includes(status ?? '') }

export default App
