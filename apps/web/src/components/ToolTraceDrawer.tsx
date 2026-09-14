import { useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import type { InvestigationTraceInfo } from '../api'

export function ToolTraceDrawer({ trace }: { trace: InvestigationTraceInfo }) {
  const [expanded, setExpanded] = useState(false)
  const modelCalls = trace.tool_calls.filter(call => call.source !== 'MANIFEST_AUTO_FILL' && !call.is_auto_filled).length
  const provenance = trace.provider && trace.model && modelCalls
    ? `${trace.provider} · ${trace.model}`
    : trace.status === 'PROVIDER_EXHAUSTED' ? 'Provider exhausted safely' : 'No verified model calls'
  return <div className="section-block investigation-block"><div className="section-title"><div><h3>Investigation trace</h3><p>{trace.rounds_used} model round{trace.rounds_used > 1 ? 's' : ''} · {modelCalls} model-selected · {trace.tool_calls.length - modelCalls} manifest auto-fill</p><small>{provenance}</small></div><div className="investigation-tags"><span className={`policy-tag ${trace.mandatory_evidence_complete ? 'pass' : 'warning'}`}><ShieldCheck size={13} />{trace.mandatory_evidence_complete ? 'MANIFEST COMPLETE' : 'MANIFEST INCOMPLETE'}</span><button className="expand-toggle" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>{expanded ? 'Hide tools' : 'View tools'}</button></div></div>{expanded && <div className="tool-calls-table"><div className="tool-calls-header"><span>Tool</span><span>Round</span><span>Status</span><span>Duration</span><span>Ref</span></div>{trace.tool_calls.map((call, index) => { const manifest = call.source === 'MANIFEST_AUTO_FILL' || call.is_auto_filled; return <div className="tool-call-row" key={`${call.call_index}-${call.tool_name}-${index}`}><span className="tool-name"><code>{call.tool_name}</code><span className="auto-tag">{manifest ? 'Manifest auto-fill' : `${call.provider ?? 'Model'} selected`}</span></span><span>{manifest ? 'Guard' : `R${call.round_number}`}</span><span className={`tool-status ${call.status.toLowerCase()}`}>{call.status}</span><span>{call.latency_ms}ms</span><span className="tool-ref" title={call.result_ref ?? ''}>{call.result_ref ? `${call.result_ref.slice(0, 16)}…` : '—'}</span></div> })}</div>}</div>
}
