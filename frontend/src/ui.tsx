import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { LoaderCircle } from 'lucide-react'
import { initials } from './api'

export function Loading({ children, inline = false }: { children: ReactNode; inline?: boolean }) {
  return <div className={inline ? 'loading-inline' : 'loading-card'} role="status">
    <span className="loading-label">{inline && <LoaderCircle className="spinner" size={18} aria-hidden="true" />}{children}</span>
    {!inline && <span className="loading-track" aria-hidden="true" />}
  </div>
}

export function LoadingPreview({ label, children }: { label: string; children: ReactNode }) {
  return <>
    <Loading inline>{label}</Loading>
    <div className="loading-preview" aria-hidden="true">{children}</div>
  </>
}

export function Skeleton({ width, height }: { width?: string; height?: string }) {
  return <span className="skeleton" style={{ width, height }} />
}

export function AppointmentLoading({ label = 'Loading appointments…' }: { label?: string }) {
  return <LoadingPreview label={label}>
    <div className="appointment-grid">
      {[0, 1].map(index => <div className="appointment-card" key={index}>
        <div className="appointment-head">
          <Skeleton width="43px" height="43px" />
          <div className="skeleton-stack"><Skeleton width="65%" /><Skeleton width="40%" /></div>
        </div>
        <div className="appointment-info"><Skeleton width="70%" /><Skeleton width="52%" /></div>
        <Skeleton width="38%" height="38px" />
      </div>)}
    </div>
  </LoadingPreview>
}

export function ActionButton({ pending, pendingLabel, disabled, children, ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { pending: boolean; pendingLabel: string }) {
  return <button {...props} disabled={disabled || pending} aria-busy={pending}>
    <span className="action-labels">
      <span className="action-label" aria-hidden={pending || undefined}>{children}</span>
      {pending && <span className="action-label pending-label"><LoaderCircle className="spinner" size={17} aria-hidden="true" />{pendingLabel}</span>}
    </span>
  </button>
}

export function Avatar({ name, large = false }: { name: string; large?: boolean }) {
  return <div className={large ? 'avatar avatar-large' : 'avatar'} aria-hidden="true">{initials(name)}</div>
}

export function EmptyState({ icon, title, text, action, level = 3 }: {
  icon: ReactNode; title: string; text: string; action?: ReactNode; level?: 1 | 3
}) {
  const Heading = level === 1 ? 'h1' : 'h3'
  return <div className="empty-state">
    <span aria-hidden="true">{icon}</span><Heading>{title}</Heading><p>{text}</p>{action}
  </div>
}
