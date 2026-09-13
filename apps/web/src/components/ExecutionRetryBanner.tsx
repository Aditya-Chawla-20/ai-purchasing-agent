import { AlertTriangle, LoaderCircle, RefreshCw } from 'lucide-react'

export function ExecutionRetryBanner({ proposalVersion, busy, onRetry }: { proposalVersion?: number; busy: boolean; onRetry: () => void }) {
  return <div className="retry-banner"><div className="retry-content"><AlertTriangle size={20} className="warning-icon" /><div><strong>Provider Execution Interrupted</strong><p>The action provider encountered a transient failure. The approved proposal v{proposalVersion} is preserved with its idempotency key.</p></div></div><button className="primary-button" onClick={onRetry} disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}Retry Execution</button></div>
}
