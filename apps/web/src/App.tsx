import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertTriangle, ArrowRight, Boxes,
  Check, CheckCircle2, ChevronDown, CircleHelp, Clock3, FileCheck2, FileText,
  FlaskConical, LayoutDashboard, LoaderCircle, MapPin, PackageCheck, PackageSearch, RefreshCw,
  ShieldCheck, Sparkles, Truck, Warehouse, X,
} from 'lucide-react'
import { api, type DemoScenario, type Evidence, type Recommendation, type Review, type TimelineEvent } from './api'

const scenarioChoices = [
  ['inventory-conflict', 'Inventory conflict'],
  ['late-incoming-po', 'Delayed incoming PO'],
  ['supplier-terms-change', 'Supplier quote change'],
  ['lost-create-response', 'Lost create response'],
  ['wrong-persisted-po', 'Wrong persisted PO'],
] as const

function App() {
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [rejectOpen, setRejectOpen] = useState(false)
  const [approveOpen, setApproveOpen] = useState(false)
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
    if (!reviewId) { setReview(null); setEvents([]); return }
    try {
      const [nextReview, nextEvents] = await Promise.all([api.review(reviewId), api.events(reviewId)])
      setReview(nextReview)
      setEvents(nextEvents.items)
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load review.') }
  }, [reviewId])

  useEffect(() => { void loadRecommendations() }, [loadRecommendations])
  useEffect(() => {
    if (!reviewId) return
    void loadReview()
    if (review?.status === 'AWAITING_APPROVAL' || isTerminal(review?.status)) return
    const timer = window.setInterval(() => { void loadReview() }, 1000)
    return () => window.clearInterval(timer)
  }, [loadReview, reviewId, review?.status])

  const selectedRecommendation = useMemo(() => {
    if (review && review.id === reviewId) return review.recommendation
    const id = recommendationId
    return recommendations.find(item => item.id === id) ?? null
  }, [recommendationId, recommendations, review])

  async function startReview() {
    if (!recommendationId) return
    setBusy(true); setError('')
    try {
      const result = await api.startReview(recommendationId)
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
      await api.approve(review.id, review.decision.version)
      setApproveOpen(false)
      setNotice('Approval recorded. Revalidating facts before execution.')
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
          <section className="page-heading shop-hero"><div className="hero-copy"><div className="location-chip"><MapPin size={15} />Bengaluru Fulfilment Centre <ChevronDown size={14} /></div><h1>Purchasing intelligence<br />for <em>every decision.</em></h1><p>Stockwise investigates demand, supply, budget, and storage before a buyer approves an order.</p><div className="hero-actions"><button className="hero-primary" onClick={() => { if (recommendationId) void startReview() }} disabled={busy || !recommendationId}>{busy ? <LoaderCircle className="spin" size={16} /> : <PackageSearch size={16} />}{busy ? 'Checking recommendation' : recommendationId ? 'Review selected item' : 'Choose an item to review'}</button><button className="hero-secondary" onClick={simulatePartial} disabled={busy}><Activity size={16} />See supplier exception</button></div></div><div className="hero-delivery planning-scene" aria-hidden="true"><span className="hero-sticker one">LIVE MODEL</span><span className="hero-sticker two">8 checks</span><div className="hero-bag"><Boxes size={49} /><b>350</b><small>SAFE UNITS</small></div><span className="hero-route" /></div></section>

          <section className="metric-grid" aria-label="Purchasing overview">
            <Metric label="Needs your review" value={recommendations.filter(x => x.review_status === 'AWAITING_APPROVAL' || !x.review_status).length.toString().padStart(2, '0')} detail="Across all locations" icon={<FileText size={17} />} tone="violet" trend="Action needed" />
            <Metric label="Decisions today" value={recommendations.filter(x => x.review_status).length.toString().padStart(2, '0')} detail="Recommendations evaluated" icon={<CheckCircle2 size={17} />} tone="green" trend="Live" />
            <Metric label="Purchase orders" value={review?.action ? '01' : '—'} detail="Created and validated" icon={<PackageCheck size={17} />} tone="blue" trend={review?.validation?.status ?? 'Awaiting action'} />
            <Metric label="Policy checks" value={review?.decision?.constraints.filter(x => x.passed).length?.toString().padStart(2, '0') ?? '—'} detail="Hard constraints passing" icon={<ShieldCheck size={17} />} tone="amber" trend={review?.decision ? 'Deterministic' : 'Ready'} />
          </section>

          {error && <div className="alert error-alert"><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error"><X size={15} /></button></div>}
          {notice && <div className="alert notice-alert"><Check size={16} />{notice}</div>}

          <section className="scenario-lab" aria-labelledby="scenario-lab-title">
            <div className="scenario-lab-title"><span className="scenario-lab-icon"><FlaskConical size={16} /></span><div><h2 id="scenario-lab-title">Scenario Lab</h2><p>Resettable proof points for Scenario 1 safety controls.</p></div></div>
            <div className="scenario-options" aria-label="Choose a demo scenario">{scenarioChoices.map(([id, label]) => <button key={id} className={scenario?.scenario === id ? 'selected' : ''} aria-pressed={scenario?.scenario === id} onClick={() => void resetScenario(id)} disabled={busy}>{label}</button>)}</div>
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
              {!selectedRecommendation ? <div className="empty-review"><PackageSearch size={28} /><h3>Select a recommendation</h3><p>Choose an item from the queue to inspect its purchasing situation.</p></div> : !review ? <RecommendationDetail item={selectedRecommendation} busy={busy} onStart={startReview} /> : <ReviewDetail review={review} events={events} busy={busy} onApprove={() => setApproveOpen(true)} onReject={() => setRejectOpen(true)} />}
            </section>
          </div>

          <footer className="page-footer"><span>Decision support powered by deterministic policy checks</span><span>DEMO ENVIRONMENT <i /></span></footer>
        </div>
      </main>

      {rejectOpen && <div className="modal-backdrop" role="presentation"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="reject-title"><button className="modal-close" onClick={() => setRejectOpen(false)} aria-label="Close"><X size={18} /></button><span className="modal-icon"><AlertTriangle size={19} /></span><h2 id="reject-title">Reject this proposal?</h2><p>The purchase order will not be created. Add a short reason for the audit trail.</p><label htmlFor="reject-reason">Reason</label><textarea id="reject-reason" value={rejectComment} onChange={e => setRejectComment(e.target.value)} placeholder="Why are you rejecting this recommendation?" rows={3} /><div className="modal-actions"><button className="secondary-button" onClick={() => setRejectOpen(false)}>Keep reviewing</button><button className="danger-button" disabled={!rejectComment.trim() || busy} onClick={rejectProposal}>{busy ? 'Submitting…' : 'Reject proposal'}</button></div></section></div>}
      {approveOpen && review?.decision && <div className="modal-backdrop" role="presentation"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="approve-title"><button className="modal-close" onClick={() => setApproveOpen(false)} aria-label="Close"><X size={18} /></button><span className="modal-icon"><ShieldCheck size={19} /></span><h2 id="approve-title">Confirm purchase order</h2><p>Approval is for this exact proposal. The system will refresh facts before creating the order.</p><div className="approval-confirmation"><span>{review.recommendation.supplier.name}</span><strong>{review.recommendation.product.sku} · {review.recommendation.node.code}</strong><b>{review.decision.proposed_quantity.toLocaleString()} units · {money(Number(review.decision.calculations.total_cost_minor ?? 0), String(review.decision.calculations.currency ?? 'INR'))}</b><small>Proposal version {review.decision.version}</small></div><div className="modal-actions"><button className="secondary-button" onClick={() => setApproveOpen(false)}>Keep reviewing</button><button className="primary-button" disabled={busy} onClick={submitApproval}>{busy ? 'Submitting…' : 'Approve exact proposal'}</button></div></section></div>}
    </div>
  )
}

function ScenarioGuide({ scenario, review, busy, onAdvance }: { scenario: DemoScenario; review: Review | null; busy: boolean; onAdvance: () => void }) {
  const guarded = scenario.scenario === 'inventory-conflict' ? review?.status === 'NEEDS_ATTENTION'
    : scenario.scenario === 'late-incoming-po' ? Number(review?.decision?.calculations.raw_need) === 600
    : scenario.scenario === 'supplier-terms-change' ? (review?.decision?.version ?? 0) > 1
    : scenario.scenario === 'lost-create-response' ? review?.validation?.status === 'PASSED'
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

function ReviewDetail({ review, events, busy, onApprove, onReject }: { review: Review; events: TimelineEvent[]; busy: boolean; onApprove: () => void; onReject: () => void }) {
  const decision = review.decision
  const statusLabel = review.status.replaceAll('_', ' ').toLowerCase()
  const calc = decision?.calculations ?? {}
  return <>
    <div className="detail-header"><div className="detail-ident"><span className="product-thumb large mint"><span>{review.recommendation.product.name.slice(0, 1)}</span><i /></span><div><div className="section-kicker">PURCHASE REVIEW <span className="review-ref">#{review.id.slice(0, 8).toUpperCase()}</span></div><h2>{review.recommendation.product.name}</h2><p>{review.recommendation.product.sku} <i /> {review.recommendation.node.name}</p></div></div><span className={`status-pill ${statusLabel.replaceAll(' ', '-')}`}>{statusLabel}</span></div>
    {!decision && <div className="progress-card"><span className="progress-orb"><LoaderCircle className="spin" size={21} /></span><div><strong>Investigating the purchasing situation</strong><p>Collecting inventory, demand, open orders, supplier terms, budget and storage capacity.</p></div></div>}
    {decision && <>
      <div className={`decision-banner ${decision.type.toLowerCase()}`}><div className="decision-symbol">{decision.type === 'INVESTIGATE' ? <AlertTriangle size={19} /> : decision.type === 'REJECT' ? <X size={19} /> : <Check size={19} />}</div><div className="decision-copy"><span className="section-kicker">DETERMINISTIC DECISION <span className="confidence">{decision.confidence} CONFIDENCE</span></span><h3>{decision.type === 'MODIFY' ? 'Adjust the recommended quantity' : decision.type === 'ACCEPT' ? 'Recommendation is supported' : decision.type === 'REJECT' ? 'No additional purchase needed' : 'More information is needed'}</h3><p><span className="ai-label">AI explanation</span> {decision.explanation.summary}</p></div><div className="decision-qty"><small>PROPOSED</small><strong>{decision.proposed_quantity.toLocaleString()}</strong><span>units</span></div></div>

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

      <div className="section-block evidence-block"><div className="section-title"><div><h3>Evidence checked</h3><p>{review.evidence.completeness === 'COMPLETE' ? 'All required sources reviewed' : 'Some evidence needs attention'}</p></div><span className={`evidence-count ${review.evidence.completeness === 'COMPLETE' ? '' : 'warning'}`}>{review.evidence.items.length} sources</span></div>
        <div className="evidence-list">{review.evidence.items.map((fact: Evidence, i: number) => <EvidenceRow key={fact.name} fact={fact} index={i} />)}</div>
        {!!review.evidence.errors.length && <div className="evidence-warning"><AlertTriangle size={15} />{review.evidence.errors.join(' · ')}</div>}
      </div>

      <div className="section-block constraints-block"><div className="section-title"><div><h3>Constraint checks</h3><p>Every hard limit is validated before execution</p></div><span className="check-summary">{decision.constraints.filter(x => x.passed).length}/{decision.constraints.length} pass</span></div><div className="constraint-list">{decision.constraints.map(check => <div className="constraint-row" key={check.code}><span className={`constraint-icon ${check.passed ? 'pass' : 'fail'}`}>{check.passed ? <Check size={12} /> : <X size={12} />}</span><span>{humanize(check.code)}</span><small>{constraintDetail(check)}</small></div>)}</div></div>

      {review.status === 'AWAITING_APPROVAL' && <div className="approval-bar"><div><strong>Ready for your decision</strong><span>Proposal v{decision.version} · {money(Number(decision.calculations.total_cost_minor ?? 0), String(decision.calculations.currency ?? 'INR'))} total</span></div><div className="approval-actions"><button className="reject-button" onClick={onReject} disabled={busy}>Reject</button><button className="primary-button" onClick={onApprove} disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}Approve & create PO</button></div></div>}

      {review.status === 'EXECUTING' || review.status === 'VALIDATING' ? <div className="progress-card compact"><span className="progress-orb"><LoaderCircle className="spin" size={19} /></span><div><strong>{review.status === 'EXECUTING' ? 'Creating purchase order' : 'Validating the purchase order'}</strong><p>Verifying the persisted result against the approved proposal.</p></div></div> : null}
      {review.action && <div className={`result-card ${review.validation?.status === 'PASSED' ? 'success' : ''}`}><div className="result-icon">{review.validation?.status === 'PASSED' ? <CheckCircle2 size={18} /> : <FileCheck2 size={18} />}</div><div><strong>{review.validation?.status === 'PASSED' ? 'Purchase order created and validated' : 'Purchase order action recorded'}</strong><p>{review.action.external_id} · {money(Number(review.action.total_minor ?? 0), review.action.currency ?? 'INR')}</p></div><span className="validation-pill">{review.validation?.status ?? 'VALIDATING'}</span></div>}
      {review.status === 'NEEDS_ATTENTION' && <div className="needs-attention"><AlertTriangle size={17} /><div><strong>Buyer attention needed</strong><p>{review.evidence.errors.join(' · ') || decision.reason_codes.map(humanize).join(' · ')}</p></div></div>}
      {review.status === 'REJECTED_BY_BUYER' && <div className="result-card muted"><X size={17} /><div><strong>Proposal rejected</strong><p>{review.approval?.comment || 'No purchase order was created.'}</p></div></div>}
      {review.status === 'COMPLETED' && !review.action && <div className="result-card success"><CheckCircle2 size={18} /><div><strong>Review complete — no purchase order created</strong><p>The recommendation was rejected because no additional inventory is required.</p></div></div>}
    </>}

    {review.status === 'NEEDS_ATTENTION' && !decision && <div className="needs-attention"><AlertTriangle size={17} /><div><strong>Recovery limit reached</strong><p>Partial fulfilment could not be safely replanned after {review.recovery_attempts ?? 0} recovery attempts. No new purchase order was created.</p></div></div>}
    {review.status === 'FAILED' && <div className="needs-attention"><AlertTriangle size={17} /><div><strong>Review could not be completed</strong><p>Refresh the review and check the activity timeline before retrying.</p></div></div>}

    <div className="timeline"><div className="section-title"><div><h3>Activity timeline</h3><p>Auditable steps in this review</p></div><span className="timeline-count">{events.length} events</span></div>{events.length ? <div className="timeline-list">{events.slice(-6).reverse().map(event => <div className="timeline-row" key={event.id}><span className="timeline-icon"><TimelineIcon type={event.event_type} /></span><div><strong>{humanize(event.event_type)}</strong><small>{new Date(event.created_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}{event.payload.quantity ? ` · ${event.payload.quantity} units` : ''}</small></div></div>)}</div> : <div className="timeline-empty">Activity will appear here as the review progresses.</div>}</div>
  </>
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

function TimelineIcon({ type }: { type: string }) { if (type.includes('DECISION')) return <Sparkles size={13} />; if (type.includes('APPROVAL')) return <Check size={13} />; if (type.includes('VALIDATION')) return <FileCheck2 size={13} />; if (type.includes('ACTION')) return <PackageCheck size={13} />; if (type.includes('ESCALATED')) return <AlertTriangle size={13} />; return <Activity size={13} /> }
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
