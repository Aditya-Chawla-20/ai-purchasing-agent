import { useEffect, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import type { InvestigationTraceInfo } from '../api'
import { readableError, toolAuditLabel, toolOutcome, toolScope } from './toolAudit'

export function ToolTraceDrawer({ trace }: { trace: InvestigationTraceInfo }) {
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    if (trace.status === 'IN_PROGRESS' || trace.status === 'PROVIDER_EXHAUSTED') setExpanded(true)
  }, [trace.status])
  const modelCalls = trace.tool_calls.filter(call => call.source !== 'MANIFEST_AUTO_FILL' && !call.is_auto_filled).length
  const provenance = trace.provider && trace.model && modelCalls
    ? `${trace.provider} · ${trace.model}`
    : trace.status === 'PROVIDER_EXHAUSTED' ? 'Provider exhausted safely' : 'No verified model calls'
  return <div className="section-block investigation-block"><div className="section-title"><div><h3>Tool activity</h3><p>{trace.rounds_used} model round{trace.rounds_used > 1 ? 's' : ''} · {modelCalls} model-selected · {trace.tool_calls.length - modelCalls} manifest auto-fill</p><small>{provenance}</small></div><div className="investigation-tags"><span className={`policy-tag ${trace.mandatory_evidence_complete ? 'pass' : 'warning'}`}><ShieldCheck size={13} />{trace.mandatory_evidence_complete ? 'MANIFEST COMPLETE' : 'MANIFEST INCOMPLETE'}</span><button className="expand-toggle" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>{expanded ? 'Hide tool log' : 'View tool log'}</button></div></div>{expanded && <div className="tool-calls-table" aria-label="Tool call log"><div className="tool-calls-header"><span>Tool and selection</span><span>Scope</span><span>Result</span><span>Time</span></div>{trace.tool_calls.map((call, index) => { const manifest = call.source === 'MANIFEST_AUTO_FILL' || call.is_auto_filled; return <div className="tool-call-row" key={`${call.call_index}-${call.tool_name}-${index}`}><span className="tool-name"><strong>{toolAuditLabel(call.tool_name)}</strong><code>{call.tool_name}</code><span className="auto-tag">{manifest ? 'Manifest auto-fill' : `${call.provider ?? 'Model'} selected`}</span></span><span className="tool-scope">{toolScope(call.arguments)}</span><span className={`tool-result ${call.status.toLowerCase()}`}><b>{call.status === 'SUCCEEDED' ? 'Completed' : 'Failed safely'}</b><small>{call.status === 'SUCCEEDED' ? toolOutcome(call) : readableError(call.error_code)}</small></span><span className="tool-timing">{manifest ? 'Guard' : `Round ${call.round_number}`}<small>{call.latency_ms}ms</small></span></div> })}</div>}</div>
}
