import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { CalendarDays, CreditCard, Hospital, MapPin, Video } from 'lucide-react'
import { Link } from 'react-router-dom'
import { ApiError, api, dateTime, doctorDisplayName, post, rupees } from './api'
import type { Appointment, CancellationQuote, Checkout, Refund } from './api'
import { PrescriptionPanel } from './Prescriptions'
import { ActionButton, Loading } from './ui'
import { ErrorBox } from './Account'

const scriptLoads = new Map<string, Promise<void>>()

export function loadScript(src: string): Promise<void> {
  const existing = scriptLoads.get(src)
  if (existing) return existing
  const loading = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = src
    script.onload = () => resolve()
    script.onerror = () => {
      script.remove()
      scriptLoads.delete(src)
      reject(new Error('Could not connect to the external service. Try again.'))
    }
    document.body.appendChild(script)
  })
  scriptLoads.set(src, loading)
  return loading
}

export async function openCheckout(checkout: Checkout, doctorName: string,
  verify: (payment: { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string }) => Promise<Appointment>) {
  const provider = window as Window & { Razorpay?: new (options: Record<string, unknown>) => { open: () => void } }
  if (!provider.Razorpay) await loadScript('https://checkout.razorpay.com/v1/checkout.js')
  const Razorpay = provider.Razorpay
  if (!Razorpay) throw new Error('Payment checkout is unavailable')
  return new Promise<Appointment>((resolve, reject) => {
    let settling = false
    const instance = new Razorpay({ key: checkout.key, amount: checkout.amount, currency: checkout.currency,
      order_id: checkout.order_id, name: 'MediConnect', description: `Consultation with ${doctorDisplayName(doctorName)}`,
      theme: { color: '#167d72' },
      handler: async (payment: { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string }) => {
        if (settling) return
        settling = true
        try { resolve(await verify(payment)) } catch (error) { reject(error) }
      },
      modal: { ondismiss: () => {
        if (settling) return
        settling = true
        reject(new Error('Checkout closed. You can resume payment before the hold expires.'))
      } },
    })
    instance.open()
  })
}

export function CancellationPolicy() {
  return <details className="cancellation-policy">
    <summary>Cancellation and refunds</summary>
    <p>Patient cancellations deduct 0% at least 24 hours before the visit, 25% from 2 to under 24 hours, and 50% in the final 2 hours. Doctor cancellations refund the full amount.</p>
  </details>
}

const refundStatus = {
  queued: 'Refund queued', processing: 'Refund processing', processed: 'Refund processed', needs_review: 'Refund needs review',
}

function CancellationDialog({ item, endpoint, quote: initial, close, onCancelled }: {
  item: Appointment; endpoint: string; quote: CancellationQuote; close: () => void; onCancelled: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [quote, setQuote] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [available, setAvailable] = useState(true)
  useEffect(() => { dialog.current?.showModal() }, [])

  const cancel = async () => {
    setBusy(true)
    setError('')
    try {
      await post(`${endpoint}/cancel`, { paid_paise: quote.paid_paise, refund_paise: quote.refund_paise })
      onCancelled()
      close()
    } catch (err) {
      setError((err as Error).message)
      if (err instanceof ApiError && err.status === 409) {
        const detail = err.detail as { code?: string; quote?: CancellationQuote } | undefined
        if (detail?.code === 'quote_changed' && detail.quote) setQuote(detail.quote)
        else setAvailable(false)
      }
    } finally { setBusy(false) }
  }

  return <dialog ref={dialog} className="cancel-dialog" aria-label="Cancel appointment" onCancel={event => {
    event.preventDefault()
    if (!busy) close()
  }}>
    <h2>Cancel appointment?</h2>
    <p>{doctorDisplayName(item.doctor_name)} · {dateTime(item.start_at)} IST</p>
    <dl className="money-summary">
      <div><dt>Amount paid</dt><dd>{rupees(quote.paid_paise)}</dd></div>
      <div><dt>Cancellation deduction{quote.paid_paise > 0 && ` (${quote.fee_percent}%)`}</dt><dd>{rupees(quote.fee_paise)}</dd></div>
      <div><dt>Test refund</dt><dd>{rupees(quote.refund_paise)}</dd></div>
    </dl>
    {item.payment_processing && <p>If your pending payment is captured after cancellation, it will be refunded in full.</p>}
    {error && <ErrorBox message={error} />}
    <div className="cancel-actions">
      <button className="outline-button small" autoFocus disabled={busy} onClick={close}>{available ? 'Keep appointment' : 'Close'}</button>
      {available && <ActionButton className="button button-small cancel-confirm" pending={busy} pendingLabel="Cancelling…"
        onClick={() => void cancel()}>Confirm cancellation</ActionButton>}
    </div>
  </dialog>
}

export function AppointmentCard({ item, role, onChanged, checkoutError }: {
  item: Appointment; role: 'patient' | 'doctor' | 'guest'; onChanged: () => void; checkoutError?: string
}) {
  const endpoint = `${role === 'guest' ? '/guest' : ''}/appointments/${item.id}`
  const [error, setError] = useState(checkoutError ?? '')
  const [message, setMessage] = useState('')
  const [quote, setQuote] = useState<CancellationQuote | null>(null)
  const [pendingAction, setPendingAction] = useState<'quote' | 'complete' | 'pay' | 'verify-payment' | 'review' | null>(null)
  const [savingPrescription, setSavingPrescription] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const start = Date.parse(item.start_at)
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (item.status !== 'confirmed' && item.status !== 'pending_payment') return
    const timer = window.setInterval(() => setNow(Date.now()), 15_000)
    return () => window.clearInterval(timer)
  }, [item.status])
  const busy = pendingAction !== null
  const canJoin = now >= start - 15 * 60_000 && now <= Date.parse(item.end_at) + 30 * 60_000
  const pending = item.status === 'pending_payment' && Date.parse(item.hold_expires_at) > now
  const canManagePrescription = pending && role === 'patient'
  const openCancellation = async () => {
    setPendingAction('quote')
    setError('')
    try { setQuote(await api<CancellationQuote>(`${endpoint}/cancellation-quote`)) }
    catch (err) { setError((err as Error).message); onChanged() }
    finally { setPendingAction(null) }
  }
  const complete = async () => {
    setPendingAction('complete')
    setError('')
    try { await post(`${endpoint}/complete`); onChanged() }
    catch (err) { setError((err as Error).message) }
    finally { setPendingAction(null) }
  }
  const resumePayment = async () => {
    setPendingAction('pay')
    setError('')
    try {
      const checkout = await api<Checkout>(`${endpoint}/checkout`)
      const updated = await openCheckout(checkout, item.doctor_name, payment => {
        setPendingAction('verify-payment')
        return post<Appointment>(`${endpoint}/verify-payment`, payment)
      })
      if (updated.status === 'confirmed') setMessage('Appointment confirmed.')
      else if (!updated.payment_processing) throw new Error('Booking status changed. Check the appointment details.')
    } catch (err) { setError((err as Error).message) }
    finally { setPendingAction(null); onChanged() }
  }
  const submitReview = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setPendingAction('review')
    setError('')
    try {
      await post(`${endpoint}/review`, { rating: Number(data.get('rating')), comment: data.get('comment') })
      setReviewOpen(false)
      onChanged()
    } catch (err) { setError((err as Error).message) }
    finally { setPendingAction(null) }
  }

  return <article className="appointment-card" id={`appointment-${item.id}`} tabIndex={-1}>
    <div className="appointment-head">
      <div className="appointment-mode-icon">{item.mode === 'online' ? <Video size={21} /> : <Hospital size={21} />}</div>
      <div><h3>{role === 'doctor' ? item.patient_name : doctorDisplayName(item.doctor_name)}</h3>
        <span>{item.mode === 'online' ? 'Video consultation' : 'Clinic visit'}{item.booking_kind === 'urgent' && ' · Urgent guest booking'}</span></div>
      <span className={`status status-${item.status}`}>{item.payment_processing ? 'Payment processing' :
        item.status === 'pending_payment' ? pending ? 'Payment due' : 'Hold ended' : item.status.replaceAll('_', ' ')}</span>
    </div>
    <div className="appointment-info">
      <span><CalendarDays size={16} /> {dateTime(item.start_at)} IST</span>
      <span><CreditCard size={16} /> {rupees(item.amount_paise)} · test payment</span>
      {item.mode === 'clinic' && <span><MapPin size={16} /> {item.clinic_name}, {item.clinic_address}</span>}
    </div>
    {item.status === 'confirmed' && item.mode === 'online' && !canJoin && now < start && <p className="booking-note">Join opens 15 minutes before the appointment.</p>}
    {pending && role !== 'doctor' && !item.payment_processing && <p className="booking-note">Reserved until {dateTime(item.hold_expires_at)} IST.</p>}
    {item.cancelled_at && <p className="booking-note">Cancelled by {item.cancelled_by === 'admin' ? 'MediConnect' : item.cancelled_by === 'doctor' ? 'the doctor' : 'the patient'} on {dateTime(item.cancelled_at)} IST.
      {!!item.cancellation_fee_paise && ` Deduction ${rupees(item.cancellation_fee_paise)}.`}</p>}
    {item.refund && <div className="refund-summary">
      <strong>{refundStatus[item.refund.status]} · {rupees(item.refund.amount_paise)}</strong>
      {item.refund.reference && <small>Reference {item.refund.reference}</small>}
      {item.refund.status === 'needs_review' && <small>The project admin can review this refund.</small>}
      {['queued', 'processing'].includes(item.refund.status) && <button className="text-button" onClick={onChanged}>Refresh refund status</button>}
    </div>}
    {role !== 'guest' && (item.prescription_status || (canManagePrescription && item.prescription_consent)) &&
      <PrescriptionPanel item={item} editable={canManagePrescription} disabled={busy}
        onChanged={onChanged} onSaving={setSavingPrescription} />}
    <div className="appointment-actions">
      {pending && role !== 'doctor' && (item.payment_processing
        ? <button className="outline-button small" onClick={onChanged}>Refresh payment status</button>
        : <ActionButton className="button button-small" pending={pendingAction === 'pay' || pendingAction === 'verify-payment'}
          pendingLabel={pendingAction === 'verify-payment' ? 'Verifying payment…' : 'Opening checkout…'}
          disabled={busy || savingPrescription} onClick={() => void resumePayment()}><CreditCard size={16} aria-hidden="true" /> Complete test payment</ActionButton>)}
      {item.status === 'confirmed' && item.mode === 'online' && canJoin && <Link className="button button-small" to={`${role === 'guest' ? '/urgent' : ''}/meeting/${item.id}`}><Video size={16} aria-hidden="true" /> Join video visit</Link>}
      {item.status === 'confirmed' && role === 'doctor' && now >= start && <ActionButton className="outline-button small"
        pending={pendingAction === 'complete'} pendingLabel="Updating…" disabled={busy} onClick={() => void complete()}>Mark completed</ActionButton>}
      {item.can_cancel && now < start && (item.status === 'confirmed' || pending) && <ActionButton className="text-button danger"
        pending={pendingAction === 'quote'} pendingLabel="Checking refund…" disabled={busy || savingPrescription}
        onClick={() => void openCancellation()}>Cancel appointment</ActionButton>}
      {item.status === 'completed' && role === 'patient' && !item.reviewed && <button className="outline-button small"
        aria-expanded={reviewOpen} aria-controls={`review-${item.id}`} onClick={() => setReviewOpen(!reviewOpen)}>
        {reviewOpen ? 'Close review form' : 'Leave a review'}</button>}
    </div>
    {reviewOpen && <form className="review-form content-enter" id={`review-${item.id}`} onSubmit={submitReview}>
      <label>Rating<select name="rating" required>{[5, 4, 3, 2, 1].map(rating => <option key={rating} value={rating}>{rating} {rating === 1 ? 'star' : 'stars'}</option>)}</select></label>
      <label>Comment<textarea name="comment" rows={3} maxLength={500} placeholder="How was your experience?" /></label>
      <ActionButton className="button button-small" pending={pendingAction === 'review'} pendingLabel="Posting review…"
        disabled={busy}>Post review</ActionButton>
    </form>}
    {message && <p className="success-box" role="status">{message}</p>}
    {error && <ErrorBox message={error} />}
    {quote && <CancellationDialog item={item} endpoint={endpoint} quote={quote} close={() => setQuote(null)} onCancelled={() => {
      setMessage('Appointment cancelled.')
      onChanged()
    }} />}
  </article>
}

type RefundReviewRow = Refund & { appointment_id: number; payment_id: string; error: string }

export function RefundReview() {
  const [rows, setRows] = useState<RefundReviewRow[] | null>(null)
  const [error, setError] = useState('')
  const refresh = async () => {
    setError('')
    try { setRows(await api<RefundReviewRow[]>('/admin/refunds')) }
    catch (err) { setError((err as Error).message) }
  }
  useEffect(() => { void refresh() }, [])
  return <section className="dashboard-section content-card refund-review">
    <div className="section-heading row"><h2>Refunds requiring review</h2><button className="text-button" onClick={() => void refresh()}>Refresh</button></div>
    {error ? <ErrorBox message={error} /> : rows === null ? <Loading inline>Loading refunds…</Loading> : rows.length === 0 ? <p>No refunds require review.</p> :
      <ul>{rows.map(refund => <li key={refund.payment_id}>
        <strong>Appointment #{refund.appointment_id} · {rupees(refund.amount_paise)}</strong>
        <p>{refund.error}. Payment {refund.payment_id}{refund.reference && ` · Refund ${refund.reference}`}</p>
      </li>)}</ul>}
  </section>
}

