import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { ApiError, api, indiaDate } from './api'
import { ErrorBox, useAuth } from './Account'
import { LoadingPreview, Skeleton } from './ui'

export type Period = 'week' | 'today' | 'last7' | 'custom'
export type ReportRange = { period: Period; start?: string; end?: string }
export type Page<T> = { items: T[]; total: number; page: number; limit: number }
export type Option = { value: string; label: string }
export type AdminAccount = {
  id: number; full_name: string; email: string; role: 'admin' | 'doctor' | 'patient'; created_at: string
  suspended_at: string | null; doctor_status: string | null; specialty: string | null
}

export function adminPath(path: string, filters: Record<string, string | number | undefined> = {}) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  return '/admin/' + path + (query.size ? '?' + query : '')
}

export function useAdminData<T>(path: string, revision: number, live = false) {
  const { setUser } = useAuth()
  const [result, setResult] = useState<{ path: string; data: T } | null>(null)
  const [error, setError] = useState<{ path: string; message: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let pending = false
    async function refresh() {
      if (pending) return
      pending = true
      setBusy(true)
      try {
        const data = await api<T>(path, { signal: controller.signal })
        if (!controller.signal.aborted) {
          setResult({ path, data })
          setError(null)
        }
      } catch (err) {
        if (controller.signal.aborted) return
        if (err instanceof ApiError && err.status === 401) setUser(null)
        setError({ path, message: (err as Error).message })
      } finally {
        pending = false
        if (!controller.signal.aborted) setBusy(false)
      }
    }
    void refresh()
    function refreshVisible() {
      if (document.visibilityState === 'visible') void refresh()
    }
    const timer = live ? window.setInterval(refreshVisible, 30_000) : undefined
    if (live) document.addEventListener('visibilitychange', refreshVisible)
    return () => {
      controller.abort()
      if (timer !== undefined) window.clearInterval(timer)
      if (live) document.removeEventListener('visibilitychange', refreshVisible)
    }
  }, [live, path, retry, revision, setUser])

  return { data: result?.path === path ? result.data : null, error: error?.path === path ? error.message : '',
    busy, reload: () => setRetry(previous => previous + 1) }
}

export function RangeControl({ range, onChange }: { range: ReportRange; onChange: (range: ReportRange) => void }) {
  const [period, setPeriod] = useState(range.period)
  const [start, setStart] = useState(range.start ?? indiaDate(new Date().toISOString()))
  const [end, setEnd] = useState(range.end ?? start)
  return <div className="admin-range">
    <label>Reporting period
      <select value={period} onChange={event => {
        const selected = event.target.value as Period
        setPeriod(selected)
        if (selected !== 'custom') onChange({ period: selected })
      }}>
        <option value="week">This week</option><option value="today">Today</option>
        <option value="last7">Last 7 days</option><option value="custom">Custom dates</option>
      </select>
    </label>
    {period === 'custom' && <form onSubmit={event => { event.preventDefault(); onChange({ period, start, end }) }}>
      <label>From<input aria-label="From date" type="date" required value={start} max={end || undefined}
        onChange={event => setStart(event.target.value)} /></label>
      <label>Through<input aria-label="Through date" type="date" required value={end} min={start || undefined}
        onChange={event => setEnd(event.target.value)} /></label>
      <button className="outline-button small">Apply dates</button>
    </form>}
  </div>
}

export function useListing() {
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [secondary, setSecondary] = useState('')
  return {
    page, q, status, secondary, setPage,
    setSearch(value: string) { setQ(value); setPage(1) },
    setStatus(value: string) { setStatus(value); setPage(1) },
    setSecondary(value: string) { setSecondary(value); setPage(1) },
  }
}

type Listing = ReturnType<typeof useListing>

export function ListFilters({ listing, search, statuses, secondary, children }: {
  listing: Listing; search: string; statuses?: Option[]; secondary?: { label: string; options: Option[] }; children?: ReactNode
}) {
  const [draft, setDraft] = useState(listing.q)
  function submit(event: FormEvent) {
    event.preventDefault()
    listing.setSearch(draft.trim())
  }
  return <div className="admin-filters">
    <form className="admin-search" onSubmit={submit}>
      <label>{search}<input type="search" value={draft} maxLength={120} onChange={event => {
        setDraft(event.target.value)
        if (!event.target.value) listing.setSearch('')
      }} /></label>
      <button className="outline-button small" aria-label="Search"><Search size={17} aria-hidden="true" /></button>
    </form>
    {statuses && <label>Status<select value={listing.status} onChange={event => listing.setStatus(event.target.value)}>
      <option value="">All statuses</option>{statuses.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select></label>}
    {secondary && <label>{secondary.label}<select value={listing.secondary} onChange={event => listing.setSecondary(event.target.value)}>
      <option value="">All</option>{secondary.options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select></label>}
    {children}
  </div>
}

export function AdminTable({ label, headings, children, compact = false }: { label: string; headings: string[]; children: ReactNode; compact?: boolean }) {
  return <>
    {!compact && <p className="admin-scroll-hint">Scroll the table sideways for more columns.</p>}
    <div className="admin-table-wrap content-enter" tabIndex={0} role="region" aria-label={label + ' table'}>
    <table className="admin-table">
      <caption className="admin-sr-only">{label}</caption>
      <thead><tr>{headings.map(title => <th key={title} scope="col">{title}</th>)}</tr></thead>
      <tbody>{children}</tbody>
    </table>
    </div>
  </>
}

export function ListResults<T>({ result, listing, empty, children }: {
  result: ReturnType<typeof useAdminData<Page<T>>>; listing: Listing; empty: string; children: (items: T[]) => ReactNode
}) {
  const { data, busy, error, reload } = result
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / (data?.limit ?? 25)))
  useEffect(() => {
    if (data && listing.page > pages) listing.setPage(pages)
  }, [data, listing.page, listing.setPage, pages])

  return <>
    {error && <div className="admin-fetch-error"><ErrorBox message={error} />
      <button className="outline-button small" onClick={reload}>Try again</button></div>}
    {!data && !error && <LoadingPreview label="Loading records…">
      <div className="admin-list-panel skeleton-stack">
        {[0, 1, 2, 3].map(index => <Skeleton key={index} height="42px" />)}
      </div>
    </LoadingPreview>}
    {data && (data.items.length ? children(data.items) : <p className="admin-empty" role="status">{empty}</p>)}
    {data && data.total > 0 && <div className="admin-pagination">
      <span>{data.total.toLocaleString('en-IN')} records · Page {listing.page} of {pages}</span>
      <div>
        <button className="outline-button small" aria-label="Previous page" disabled={listing.page <= 1 || busy}
          onClick={() => listing.setPage(listing.page - 1)}><ChevronLeft size={17} /></button>
        <button className="outline-button small" aria-label="Next page" disabled={listing.page >= pages || busy}
          onClick={() => listing.setPage(listing.page + 1)}><ChevronRight size={17} /></button>
      </div>
    </div>}
  </>
}

export function Status({ value }: { value: string }) {
  const positive = ['active', 'available', 'booked', 'confirmed', 'completed', 'processed', 'published']
  const caution = ['pending', 'pending_payment', 'held', 'queued', 'processing', 'needs_review']
  const negative = ['suspended', 'cancelled', 'rejected', 'payment_failed']
  const tone = positive.includes(value) ? 'good' : caution.includes(value) ? 'warning' : negative.includes(value) ? 'bad' : 'neutral'
  return <span className={'admin-status ' + tone}>{value.replaceAll('_', ' ')}</span>
}

export function AdminDialog({ title, busy, onClose, children }: {
  title: string; busy: boolean; onClose: () => void; children: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    return () => { dialog?.close() }
  }, [])
  return <dialog className="admin-dialog" ref={ref} aria-labelledby="admin-dialog-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
    <h2 id="admin-dialog-title">{title}</h2>
    {children}
  </dialog>
}
