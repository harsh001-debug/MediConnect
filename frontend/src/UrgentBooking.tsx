import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight, CalendarDays, Hospital, Phone, Video } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ApiError, api, dateTime, doctorDisplayName, post, rupees } from './api'
import type { Appointment, Checkout, VisitMode } from './api'
import { AppointmentCard, CancellationPolicy, openCheckout } from './Appointments'
import { ActionButton, AppointmentLoading, EmptyState, Loading, LoadingPreview, Skeleton } from './ui'
import { ErrorBox, Field } from './Account'

type GuestSession = { phone: string }
type GuestChallenge = { challenge: string; expires_at: string; resend_at: string }
type AvailableVisit = {
  id: number; doctor_name: string; specialty: string; mode: VisitMode; start_at: string
  city: string; clinic_name: string; amount_paise: number
}

function useGuestSession() {
  const [session, setSession] = useState<GuestSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    api<GuestSession>('/guest/session', { signal: controller.signal }).then(setSession).catch(err => {
      if (!controller.signal.aborted && (!(err instanceof ApiError) || err.status !== 401)) setError(err.message)
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [])
  return { session, setSession, loading, error }
}

function GuestPhoneVerification({ onVerified }: { onVerified: (session: GuestSession) => void }) {
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [challenge, setChallenge] = useState<GuestChallenge | null>(null)
  const [pending, setPending] = useState<'send' | 'verify' | null>(null)
  const [error, setError] = useState('')
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!challenge) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [challenge])
  const delay = challenge ? Math.max(0, Math.ceil((Date.parse(challenge.resend_at) - now) / 1000)) : 0
  const expired = !!challenge && Date.parse(challenge.expires_at) <= now
  const busy = pending !== null

  const sendCode = async () => {
    setPending('send')
    setError('')
    try {
      setChallenge(await post<GuestChallenge>('/guest/otp/send', { phone }))
      setCode('')
      setNow(Date.now())
    } catch (err) {
      setError((err as Error).message)
      if (err instanceof ApiError && err.status === 503) {
        setChallenge(null)
        setCode('')
      }
    }
    finally { setPending(null) }
  }
  const verify = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!challenge) return
    setPending('verify')
    setError('')
    try { onVerified(await post<GuestSession>('/guest/otp/verify', { phone, code, challenge: challenge.challenge })) }
    catch (err) { setError((err as Error).message) }
    finally { setPending(null) }
  }

  return <div className="guest-verification">
    {error && <ErrorBox message={error} />}
    {challenge ? <form className="account-form" onSubmit={verify}>
      <p role="status">{expired ? 'This code has expired. Request a new code.' : `Check ${phone} for a six-digit SMS code.`}</p>
      <Field label="6-digit SMS code" name="code" autoComplete="one-time-code" inputMode="numeric" maxLength={6} pattern="[0-9]{6}" value={code}
        onChange={event => setCode(event.target.value.replace(/[^0-9]/g, '').slice(0, 6))} autoFocus required />
      <ActionButton className="button full" pending={pending === 'verify'} pendingLabel="Checking code…"
        disabled={busy || expired || code.length !== 6}>Verify mobile number</ActionButton>
      <div className="guest-inline-actions">
        <ActionButton type="button" className="text-button" pending={pending === 'send'} pendingLabel="Sending code…"
          disabled={busy || delay > 0} onClick={() => void sendCode()}>{delay ? `Resend in ${delay}s` : 'Resend code'}</ActionButton>
        <button type="button" className="text-button" disabled={busy} onClick={() => { setChallenge(null); setError('') }}>Change number</button>
      </div>
    </form> : <form className="account-form" onSubmit={event => { event.preventDefault(); void sendCode() }}>
      <Field label="Mobile number" name="phone" value={phone} onChange={event => setPhone(event.target.value)} type="tel"
        autoComplete="tel" maxLength={32} placeholder="+91 98765 43210" required />
      <ActionButton className="button full" pending={busy} pendingLabel="Sending SMS code…">Send SMS code</ActionButton>
    </form>}
  </div>
}

function EmergencyNotice() {
  return <aside className="emergency-notice" aria-label="Emergency assistance">
    <p><strong>Need emergency help now?</strong> Call India’s emergency services immediately.</p>
    <a className="outline-button small" href="tel:112"><Phone size={16} /> Call 112</a>
  </aside>
}

function VisitDetails({ visit }: { visit: AvailableVisit }) {
  return <>
    <span className="appointment-mode-icon">{visit.mode === 'online' ? <Video size={21} /> : <Hospital size={21} />}</span>
    <div><strong>{doctorDisplayName(visit.doctor_name)}</strong><span>{visit.specialty} · {visit.mode === 'online' ? 'Video consultation' : `${visit.clinic_name}, ${visit.city}`}</span></div>
    <div className="urgent-visit-time"><strong>{dateTime(visit.start_at)} IST</strong><span>{rupees(visit.amount_paise)}</span></div>
  </>
}

export function UrgentBooking() {
  const [visits, setVisits] = useState<AvailableVisit[] | null>(null)
  const [selected, setSelected] = useState<AvailableVisit | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [bookingStatus, setBookingStatus] = useState('')
  const busy = bookingStatus !== ''
  const [refreshing, setRefreshing] = useState(true)
  const [availabilityError, setAvailabilityError] = useState('')
  const [revision, setRevision] = useState(0)
  const { session, setSession, loading: restoring, error: sessionError } = useGuestSession()
  const listHeading = useRef<HTMLHeadingElement>(null)
  const checkoutHeading = useRef<HTMLHeadingElement>(null)
  const contactHeading = useRef<HTMLHeadingElement>(null)
  const navigate = useNavigate()
  useEffect(() => {
    const controller = new AbortController()
    setRefreshing(true)
    setAvailabilityError('')
    api<AvailableVisit[]>('/guest/availability', { signal: controller.signal }).then(rows => {
      if (!controller.signal.aborted) setVisits(rows)
    }).catch(err => {
      if (!controller.signal.aborted) setAvailabilityError(err.message)
    }).finally(() => {
      if (!controller.signal.aborted) setRefreshing(false)
    })
    return () => controller.abort()
  }, [revision])

  function choose(visit: AvailableVisit | null) {
    setSelected(visit)
    setError('')
    requestAnimationFrame(() => (visit ? checkoutHeading : listHeading).current?.focus())
  }

  const book = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selected || !session) return
    setBookingStatus('Reserving time…')
    setError('')
    let reserved = false
    try {
      const { appointment, checkout } = await post<{ appointment: Appointment; checkout: Checkout }>('/guest/appointments', {
        slot_id: selected.id, full_name: name, expected_amount_paise: selected.amount_paise,
      })
      const endpoint = `/guest/appointments/${appointment.id}`
      reserved = true
      setBookingStatus('Opening checkout…')
      const updated = await openCheckout(checkout, appointment.doctor_name, payment => {
        setBookingStatus('Verifying payment…')
        return post<Appointment>(`${endpoint}/verify-payment`, payment)
      })
      navigate('/urgent/manage', { replace: true, state: updated.status === 'confirmed' ? { notice: 'Appointment confirmed.' } : null })
    } catch (err) {
      const message = (err as Error).message
      if (reserved) {
        navigate('/urgent/manage', { replace: true, state: { error: message } })
      } else if (err instanceof ApiError && err.status === 409) {
        choose(null)
        setRevision(value => value + 1)
        setError(message)
      } else {
        if (err instanceof ApiError && err.status === 401) setSession(null)
        setError(message)
      }
    } finally { setBookingStatus('') }
  }

  return <div className="page-shell shell urgent-page">
    <div className="page-intro"><h1>Book an urgent appointment</h1><p>Find a visit in the next 24 hours. Use your mobile number to book as a guest.</p></div>
    <EmergencyNotice />
    <div className="guest-inline-actions"><Link to="/urgent/manage">Manage a guest booking</Link></div>
    {error && <ErrorBox message={error} />}
    {selected ? <section className="content-card urgent-checkout content-enter" aria-labelledby="guest-checkout-title">
      <div className="section-heading row"><h2 id="guest-checkout-title" ref={checkoutHeading} tabIndex={-1}>Your selected visit</h2>
        <button className="text-button" disabled={busy} onClick={() => choose(null)}>Change time</button></div>
      <div className="urgent-visit"><VisitDetails visit={selected} /></div>
      {restoring ? <Loading inline>Checking your guest session…</Loading> : <div className="urgent-contact">
        <h3 ref={contactHeading} tabIndex={-1}>{session ? 'Review and pay' : 'Verify your mobile number'}</h3>
        {sessionError && !session && <ErrorBox message={sessionError} />}
        <form className="account-form" id="urgent-checkout" onSubmit={book}>
          <Field label="Patient’s full name" name="full_name" maxLength={120} minLength={2} autoComplete="name" value={name} onChange={event => setName(event.target.value)} required />
          {session && <><p role="status">Mobile number verified · ending in {session.phone.slice(-4)}</p>
            <ActionButton className="button full" pending={busy} pendingLabel={bookingStatus}>
              Continue to test checkout · {rupees(selected.amount_paise)}
            </ActionButton></>}
        </form>
        {!session && <GuestPhoneVerification onVerified={verified => {
          setSession(verified)
          requestAnimationFrame(() => contactHeading.current?.focus())
        }} />}
        <p className="guest-payment-note">We reserve your time at checkout. Test payments use no real money.</p>
        <CancellationPolicy />
      </div>}
    </section> : <section aria-labelledby="urgent-slots-title">
      <div className="section-heading row"><h2 id="urgent-slots-title" ref={listHeading} tabIndex={-1}>Available times</h2>
        <ActionButton className="outline-button small" pending={refreshing && visits !== null} pendingLabel="Refreshing times…"
          disabled={refreshing} onClick={() => setRevision(value => value + 1)}>Refresh times</ActionButton></div>
      {availabilityError && <ErrorBox message={availabilityError} />}
      {visits === null ? refreshing && <LoadingPreview label="Looking for appointments…">
        <div className="skeleton-stack">{[0, 1, 2].map(index => <div className="urgent-visit urgent-visit-skeleton" key={index}>
          <Skeleton width="43px" height="43px" />
          <div className="skeleton-stack"><Skeleton width="64%" /><Skeleton width="44%" /></div>
          <Skeleton width="64px" />
        </div>)}</div>
      </LoadingPreview> : visits.length === 0 ?
        <EmptyState icon={<CalendarDays />} title="No visits in the next 24 hours" text="Browse the directory for other dates."
          action={<Link className="outline-button small" to="/doctors">Browse doctors</Link>} /> :
        <ul className="urgent-visits content-enter">{visits.map(visit => <li key={visit.id}>
          <button className="urgent-visit" disabled={refreshing} onClick={() => choose(visit)}><VisitDetails visit={visit} /><ArrowRight size={18} aria-hidden="true" /></button>
        </li>)}</ul>}
    </section>}
  </div>
}

export function GuestManage() {
  const { session, setSession, loading, error: sessionError } = useGuestSession()
  const [items, setItems] = useState<Appointment[] | null>(null)
  const [error, setError] = useState('')
  const { state } = useLocation() as { state: { notice?: string; error?: string } | null }
  const [notice, setNotice] = useState(state?.notice ?? '')
  const [checkoutError, setCheckoutError] = useState(state?.error ?? '')
  const [ending, setEnding] = useState(false)
  const refresh = useCallback(async () => {
    setError('')
    try { setItems(await api<Appointment[]>('/guest/appointments')) }
    catch (err) {
      if (err instanceof ApiError && err.status === 401) { setSession(null); setItems(null) }
      else setError((err as Error).message)
    }
  }, [setSession])
  useEffect(() => { if (session) void refresh() }, [session, refresh])
  const signOut = async () => {
    setEnding(true)
    try {
      await post('/guest/sign-out')
      setSession(null)
      setItems(null)
      setNotice('')
      setCheckoutError('')
      setError('')
    } catch (err) { setError((err as Error).message) }
    finally { setEnding(false) }
  }

  return <div className="page-shell shell urgent-page">
    <div className="page-intro"><h1>Guest bookings</h1><p>{session ? `Appointments for the mobile number ending in ${session.phone.slice(-4)}.` : 'Verify the mobile number you used to book.'}</p></div>
    <div className="guest-inline-actions"><Link to="/urgent">Book an urgent appointment</Link>
      {session && <ActionButton className="text-button" pending={ending} pendingLabel="Ending session…" onClick={() => void signOut()}>End guest session</ActionButton>}</div>
    {(error || sessionError) && <ErrorBox message={error || sessionError} />}
    {loading ? <Loading>Checking your guest session…</Loading> : !session ? <div className="content-card guest-recovery"><GuestPhoneVerification onVerified={setSession} /></div> : <>
      {notice && <p className="success-box" role="status">{notice}</p>}
      {checkoutError && <ErrorBox message={checkoutError} />}
      {error && <button className="outline-button small" onClick={() => void refresh()}>Try again</button>}
      {items === null ? !error && <AppointmentLoading label="Loading bookings…" /> : items.length === 0 ?
        <EmptyState icon={<CalendarDays />} title="No guest bookings" text="We couldn’t find a booking for this mobile number."
          action={<Link className="outline-button small" to="/urgent">See available visits</Link>} /> : <div className="appointment-grid content-enter">
        {items.map(item => <AppointmentCard key={item.id} item={item} role="guest" onChanged={() => { setCheckoutError(''); setNotice(''); void refresh() }} />)}
      </div>}
    </>}
  </div>
}
