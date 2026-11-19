import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Bell, ArrowRight, LoaderCircle, X } from 'lucide-react'
import { ApiError, api, dateTime } from './api'
import type { Notice, NotificationInbox } from './api'
import { Loading } from './ui'

const INBOX_PATH = '/notifications/inbox'
const REFRESH_INTERVAL = 30_000
const noticeRowId = (id: number) => `notification-row-${id}`

function noticeHref(target: Notice['target']) {
  if (target?.type === 'appointment') return `/dashboard?appointment=${target.appointment_id}`
  if (target?.type === 'doctor_profile') return '/dashboard?tab=profile'
  return null
}

export function Notifications({ onOpen }: { onOpen: () => void }) {
  const { key } = useLocation()
  const [open, setOpen] = useState(false)
  const [inbox, setInbox] = useState<NotificationInbox | null>(null)
  const [error, setError] = useState('')
  const [loadingMore, setLoadingMore] = useState(false)
  const [saving, setSaving] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const reading = useRef<AbortController | null>(null)
  const writing = useRef<AbortController | null>(null)
  const visibleCount = useRef(0)
  const focusAfterUpdate = useRef<{ source: HTMLButtonElement; target: string } | null>(null)

  const load = useCallback(async (beforeId?: number, source?: HTMLButtonElement) => {
    if (writing.current || (reading.current && beforeId === undefined)) return
    reading.current?.abort()
    const controller = new AbortController()
    reading.current = controller
    const options = { signal: controller.signal }
    setError('')
    setLoadingMore(beforeId !== undefined)
    try {
      if (beforeId !== undefined) {
        const page = await api<NotificationInbox>(`${INBOX_PATH}?before_id=${beforeId}`, options)
        if (controller.signal.aborted) return
        if (source && document.activeElement === source && page.items.length) {
          focusAfterUpdate.current = { source, target: noticeRowId(page.items[0].id) }
        }
        visibleCount.current += page.items.length
        setInbox(current => current && { ...current, items: [...current.items, ...page.items],
          next_before_id: page.next_before_id })
        return
      }
      const latest = await api<NotificationInbox>(INBOX_PATH, options)
      const items = [...latest.items]
      let cursor = latest.next_before_id
      while (cursor !== null && items.length < visibleCount.current) {
        const page = await api<NotificationInbox>(`${INBOX_PATH}?before_id=${cursor}`, options)
        items.push(...page.items)
        cursor = page.next_before_id
      }
      if (controller.signal.aborted) return
      visibleCount.current = items.length
      setInbox({ ...latest, items, next_before_id: cursor })
    } catch (err) {
      if (controller.signal.aborted) return
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        visibleCount.current = 0
        setInbox(null)
      }
      setError((err as Error).message)
    } finally {
      if (reading.current === controller) {
        reading.current = null
        setLoadingMore(false)
      }
    }
  }, [])

  useEffect(() => {
    const refresh = () => { if (!document.hidden) void load() }
    refresh()
    const poll = window.setInterval(refresh, REFRESH_INTERVAL)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      window.clearInterval(poll)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
      reading.current?.abort()
      writing.current?.abort()
      reading.current = null
      writing.current = null
    }
  }, [load])

  useEffect(() => { setOpen(false) }, [key])
  useEffect(() => {
    const focus = focusAfterUpdate.current
    focusAfterUpdate.current = null
    if (!open || !focus) return
    const active = document.activeElement
    if (active === focus.source || (!focus.source.isConnected && active === document.body)) {
      document.getElementById(focus.target)?.focus()
    }
  }, [open, inbox])
  useEffect(() => {
    if (!open) return
    const dismissOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !wrap.current?.contains(event.target)) setOpen(false)
    }
    const dismissWithEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      trigger.current?.focus()
    }
    document.addEventListener('pointerdown', dismissOutside)
    document.addEventListener('keydown', dismissWithEscape)
    return () => {
      document.removeEventListener('pointerdown', dismissOutside)
      document.removeEventListener('keydown', dismissWithEscape)
    }
  }, [open])

  async function markRead(noticeId?: number, source?: HTMLButtonElement) {
    if (writing.current) return
    const throughId = inbox?.through_id
    if (noticeId === undefined && throughId == null) return
    reading.current?.abort()
    reading.current = null
    setLoadingMore(false)
    const controller = new AbortController()
    writing.current = controller
    setSaving(true)
    setError('')
    try {
      const path = noticeId === undefined ? '/notifications/read-all' : `/notifications/${noticeId}/read`
      const body = noticeId === undefined ? JSON.stringify({ through_id: throughId }) : undefined
      await api(path, { method: 'POST', body, signal: controller.signal })
      if (controller.signal.aborted) return
      if (source && document.activeElement === source) {
        focusAfterUpdate.current = { source, target: noticeId === undefined ? 'notification-title' : noticeRowId(noticeId) }
      }
      writing.current = null
      await load()
    } catch (err) {
      if (!controller.signal.aborted) {
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) setInbox(null)
        setError((err as Error).message)
      }
    } finally {
      if (!controller.signal.aborted) {
        if (writing.current === controller) writing.current = null
        setSaving(false)
      }
    }
  }

  function toggle() {
    if (!open) {
      onOpen()
      void load()
    }
    setOpen(!open)
  }

  const unread = inbox?.unread_count ?? 0
  return <div className="notice-wrap" ref={wrap}>
    <button type="button" className="notice-toggle" ref={trigger} onClick={toggle}
      aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}
      aria-expanded={open} aria-controls="notification-inbox">
      <Bell size={20} aria-hidden="true" />
      {unread > 0 && <span aria-hidden="true">{unread > 99 ? '99+' : unread}</span>}
    </button>
    <section className="notice-popover" id="notification-inbox" aria-labelledby="notification-title" hidden={!open}>
      <div className="notice-heading">
        <h2 id="notification-title" tabIndex={-1}>Notifications</h2>
        <div>
          {!!unread && <button type="button" className="text-button" aria-disabled={saving}
            onClick={event => { if (!saving) void markRead(undefined, event.currentTarget) }}>Mark all as read</button>}
          <button type="button" aria-label="Close notifications" className="text-button notice-close"
            onClick={() => { setOpen(false); trigger.current?.focus() }}><X size={18} aria-hidden="true" /></button>
        </div>
      </div>
      {error && <div className="notice-error"><p role="alert">{error}</p>
        <button type="button" className="text-button" onClick={() => void load()}>Try again</button>
      </div>}
      {!inbox && !error && <Loading inline>Loading notifications…</Loading>}
      {saving && <Loading inline>Marking notifications as read…</Loading>}
      {inbox && inbox.items.length === 0 && <p>No notifications yet.</p>}
      {!!inbox?.items.length && <ul className="notice-list">
        {inbox.items.map(item => {
          const href = noticeHref(item.target)
          return <li key={item.id} id={noticeRowId(item.id)} tabIndex={-1} className={`notice-item${item.read_at ? '' : ' unread'}`}>
            <div className="notice-message">
              {!item.read_at && <span className="notice-unread-label">Unread</span>}
              <p>{item.body}</p>
              <time dateTime={item.created_at}>{dateTime(item.created_at)}</time>
            </div>
            <div className="notice-actions">
              {href && <Link to={href} onClick={() => { if (!item.read_at) void markRead(item.id) }}>
                {item.target?.type === 'appointment' ? 'View appointment' : 'View profile'} <ArrowRight size={14} aria-hidden="true" />
              </Link>}
              {!item.read_at && <button type="button" className="text-button" aria-disabled={saving}
                aria-label={`Mark as read: ${item.body}`}
                onClick={event => { if (!saving) void markRead(item.id, event.currentTarget) }}>Mark as read</button>}
            </div>
          </li>
        })}
      </ul>}
      {inbox?.next_before_id != null && <button type="button" className="text-button notice-more" aria-disabled={loadingMore || saving}
        onClick={event => { if (!loadingMore && !saving) void load(inbox.next_before_id ?? undefined, event.currentTarget) }}>
        {loadingMore ? <><LoaderCircle size={15} className="spinner" aria-hidden="true" /> Loading older notifications…</> : 'Load older notifications'}
      </button>}
    </section>
  </div>
}
