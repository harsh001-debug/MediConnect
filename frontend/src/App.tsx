import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowRight, BadgeCheck, CalendarDays, Check, CreditCard, HeartPulse, Hospital, MapPin, Menu, Search, Star, Stethoscope, Video, X } from 'lucide-react'
import { api, ApiError, dateOnly, dateTime, doctorDisplayName, indiaDate, post, rupees } from './api'
import type { Appointment, Checkout, Doctor, Slot, User, VisitMode } from './api'
import { accountPath, AuthContext, ErrorBox, ForgotPassword, Login, Signup, useAuth, VerifyContacts } from './Account'
import { CancellationPolicy, loadScript, openCheckout } from './Appointments'
import { AdminDashboard } from './AdminDashboard'
import { GuestManage, UrgentBooking } from './UrgentBooking'
import { emptyPrescription, PrescriptionPicker, removePrescription, savePrescription } from './Prescriptions'
import { Notifications } from './Notifications'
import { Explore, Home, Rating } from './Directory'
import { DoctorDashboard, PatientDashboard } from './Dashboards'
import { ActionButton, Avatar, EmptyState, Loading, LoadingPreview, Skeleton } from './ui'

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)
  const { pathname } = useLocation()
  useEffect(() => {
    const controller = new AbortController()
    api<User>('/auth/me', { signal: controller.signal }).then(setUser).catch(() => {}).finally(() => {
      if (!controller.signal.aborted) setReady(true)
    })
    return () => controller.abort()
  }, [])
  useEffect(() => {
    if (!ready) return
    window.scrollTo(0, 0)
    document.getElementById('main-content')?.focus({ preventScroll: true })
  }, [pathname, ready])
  if (!ready) return <main className="app-loading">
    <span className="brand" aria-hidden="true"><span className="brand-icon"><HeartPulse size={23} /></span><span>Medi<span>Connect</span></span></span>
    <Loading>Loading MediConnect…</Loading>
  </main>
  return <AuthContext.Provider value={{ user, setUser }}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <Header />
    <main id="main-content" tabIndex={-1}><Routes>
      <Route path="/" element={<Home />} />
      <Route path="/doctors" element={<Explore />} />
      <Route path="/doctors/:id" element={<DoctorDetail />} />
      <Route path="/login" element={<Login />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/urgent" element={<UrgentBooking />} />
      <Route path="/urgent/manage" element={<GuestManage />} />
      <Route path="/urgent/meeting/:id" element={<Meeting guest />} />
      <Route path="/verify-contacts" element={<VerifyContacts />} />
      <Route path="/dashboard" element={<RequireAccount>{user?.role === 'doctor' ? <DoctorDashboard key={user.id} /> : <PatientDashboard key={user?.id} />}</RequireAccount>} />
      <Route path="/admin/:section?" element={<RequireAdmin><AdminDashboard /></RequireAdmin>} />
      <Route path="/meeting/:id" element={<RequireAccount><Meeting /></RequireAccount>} />
      <Route path="*" element={<NotFound />} />
    </Routes></main>
    <Footer />
  </AuthContext.Provider>
}

function RequireAccount({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const location = useLocation()
  const next = location.pathname + location.search
  if (!user) return <Navigate to={accountPath('login', next)} replace />
  if (user.verification_required) return <Navigate to={accountPath('verify-contacts', next)} replace />
  return children
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const { pathname, search } = useLocation()
  if (!user) return <Navigate to={accountPath('login', pathname + search)} replace />
  if (user.role !== 'admin') return <Navigate to="/dashboard" replace />
  return children
}

function Header() {
  const { user } = useAuth()
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const [loggingOut, setLoggingOut] = useState(false)
  const menuButton = useRef<HTMLButtonElement>(null)
  const { pathname } = useLocation()
  useEffect(() => { setOpen(false) }, [pathname])
  const logout = async () => {
    setLoggingOut(true)
    setError('')
    try {
      await post('/auth/logout')
      window.location.replace('/')
    } catch (err) {
      setError((err as Error).message)
      setLoggingOut(false)
    }
  }
  return <header className="site-header" onKeyDown={event => {
    if (event.key === 'Escape' && open) {
      setOpen(false)
      menuButton.current?.focus()
    }
  }}><div className="shell nav-wrap">
    <Link className="brand" to="/"><span className="brand-icon"><HeartPulse size={23} strokeWidth={2.2} /></span><span>Medi<span>Connect</span></span></Link>
    <nav id="primary-navigation" aria-label="Main navigation" className={open ? 'nav-links open' : 'nav-links'}>
      <NavLink to="/doctors" onClick={() => setOpen(false)}>Find doctors</NavLink>
      {user && <NavLink to={user.verification_required ? '/verify-contacts' : user.role === 'admin' ? '/admin' : '/dashboard'} onClick={() => setOpen(false)}>{user.verification_required ? 'Verify account' : user.role === 'admin' ? 'Admin panel' : user.role === 'doctor' ? 'My practice' : 'My appointments'}</NavLink>}
      {user?.role === 'admin' && <NavLink to="/dashboard" onClick={() => setOpen(false)}>My appointments</NavLink>}
      {user && <ActionButton className="text-button mobile-sign-out" pending={loggingOut} pendingLabel="Signing out…" onClick={logout}>Sign out</ActionButton>}
      <NavLink to="/urgent" onClick={() => setOpen(false)}>Guest booking</NavLink>
      {!user && <Link to="/signup?role=doctor" onClick={() => setOpen(false)}>For doctors</Link>}
      {!user && <Link className="mobile-join" to="/signup" onClick={() => setOpen(false)}>Create an account</Link>}
    </nav>
    <div className="nav-actions">{user ? <>
      {!user.verification_required && <Notifications key={user.id} onOpen={() => setOpen(false)} />}
      <ActionButton className="text-button nav-sign-out" pending={loggingOut} pendingLabel="Signing out…" onClick={logout}>Sign out</ActionButton>
    </> : <><Link className="text-button" to="/login" onClick={() => setOpen(false)}>Sign in</Link><Link className="button button-small" to="/signup">Create an account</Link></>}</div>
    <button ref={menuButton} className="mobile-menu" aria-label={open ? 'Close menu' : 'Open menu'} aria-expanded={open}
      aria-controls="primary-navigation" onClick={event => {
        setOpen(!open)
        if (!open && event.detail === 0) requestAnimationFrame(() => document.querySelector<HTMLAnchorElement>('#primary-navigation a')?.focus())
      }}>{open ? <X size={22} /> : <Menu size={22} />}</button>
  </div>{error && <div className="shell"><ErrorBox message={error} /></div>}</header>
}

function Footer() {
  const { user } = useAuth()
  return <footer className="site-footer"><div className="shell footer-grid">
    <div><Link className="brand" to="/"><span className="brand-icon"><HeartPulse size={22} /></span><span>Medi<span>Connect</span></span></Link></div>
    <div><Link to="/doctors">Find doctors</Link><Link to="/urgent/manage">Manage a guest booking</Link>{!user && <Link to="/signup?role=doctor">Register as a doctor</Link>}</div>
  </div><div className="shell footer-bottom">© {new Date().getFullYear()} MediConnect</div></footer>
}

type DoctorDetailData = Doctor & { prescription_consent?: string | null }
type BookingReservation = { appointment: Appointment; checkout: Checkout }
const visitTime = new Intl.DateTimeFormat('en-IN', { timeStyle: 'short', timeZone: 'Asia/Kolkata' })
const visitDate = new Intl.DateTimeFormat('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' })

function formatVisitDate(day: string) {
  const date = new Date(day + 'T00:00:00+05:30')
  return Number.isNaN(date.getTime()) ? day : visitDate.format(date)
}

function DoctorDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const { state } = useLocation() as { state: { directoryQuery?: string } | null }
  const [params, setParams] = useSearchParams()
  const [doctor, setDoctor] = useState<DoctorDetailData | null>(null)
  const [slots, setSlots] = useState<Slot[] | null>(null)
  const [profileError, setProfileError] = useState('')
  const [slotError, setSlotError] = useState('')
  const [profileRevision, setProfileRevision] = useState(0)
  const [slotRevision, setSlotRevision] = useState(0)
  const [bookingStatus, setBookingStatus] = useState('')
  const [error, setError] = useState('')
  const [prescription, setPrescription] = useState(emptyPrescription)
  const [reservation, setReservation] = useState<BookingReservation | null>(null)
  const mode: VisitMode = params.get('mode') === 'online' ? 'online' : 'clinic'
  const date = params.get('available_on')
  const modeSlots = slots?.filter(slot => slot.mode === mode) ?? []
  const selected = modeSlots.find(slot => String(slot.id) === params.get('slot') && (!date || indiaDate(slot.start_at) === date))
  const dates = [...new Set(modeSlots.map(slot => indiaDate(slot.start_at)))].sort()
  const shownDate = date || (selected && indiaDate(selected.start_at)) || dates[0] || ''
  const visibleSlots = modeSlots.filter(slot => indiaDate(slot.start_at) === shownDate)
  const selectedMissing = !!params.get('slot') && slots !== null && !selected
  const directory = { pathname: '/doctors', search: state?.directoryQuery ?? '' }
  const fee = rupees((mode === 'clinic' ? doctor?.clinic_fee_rupees ?? 0 : doctor?.online_fee_rupees ?? 0) * 100)
  const busy = bookingStatus !== ''

  useEffect(() => {
    const controller = new AbortController()
    setDoctor(null)
    setProfileError('')
    setError('')
    setPrescription(emptyPrescription)
    setReservation(null)
    api<DoctorDetailData>(`/doctors/${id}`, { signal: controller.signal }).then(setDoctor).catch(err => {
      if (!controller.signal.aborted) setProfileError(err.message)
    })
    return () => controller.abort()
  }, [id, profileRevision])

  useEffect(() => {
    const controller = new AbortController()
    setSlots(null)
    setSlotError('')
    api<Slot[]>(`/doctors/${id}/slots`, { signal: controller.signal }).then(setSlots).catch(err => {
      if (!controller.signal.aborted) setSlotError(err.message)
    })
    return () => controller.abort()
  }, [id, slotRevision])

  const choose = (kind: VisitMode, slot?: Slot, nextDate = date) => {
    setError('')
    const updated = new URLSearchParams(params)
    updated.set('mode', kind)
    if (slot) updated.set('slot', String(slot.id))
    else updated.delete('slot')
    if (nextDate) updated.set('available_on', nextDate)
    else updated.delete('available_on')
    setParams(updated, { replace: true, state })
  }

  const book = async (skipPrescription = false) => {
    if ((!selected && !reservation) || !doctor) return
    const next = `/doctors/${id}?${params}`
    if (!user) { navigate(accountPath('login', next), { state }); return }
    if (user.role !== 'patient' && user.role !== 'admin') return
    if (user.verification_required) { navigate(accountPath('verify-contacts', next), { state }); return }
    const consentVersion = doctor.prescription_consent
    const attach = !skipPrescription && prescription.photos.length > 0 && consentVersion
    if (attach && !prescription.consent) return
    setBookingStatus('Reserving time…')
    setError('')
    let checkoutAppointmentId: number | undefined
    try {
      let result = reservation
      if (!result && selected) {
        result = await post<BookingReservation>('/appointments', { slot_id: selected.id })
        setSlots(rows => rows?.filter(slot => slot.id !== selected.id) ?? [])
        choose(mode)
      }
      if (!result) return
      const appointmentId = result.appointment.id
      if (attach) {
        setBookingStatus('Uploading photos…')
        setReservation(result)
        await savePrescription(appointmentId, prescription, attach)
      } else if (reservation) {
        setBookingStatus('Removing photos…')
        await removePrescription(appointmentId)
      }
      setReservation(null)
      setPrescription(emptyPrescription)
      checkoutAppointmentId = appointmentId
      setBookingStatus('Opening checkout…')
      const appointment = await openCheckout(result.checkout, doctor.full_name,
        payment => {
          setBookingStatus('Verifying payment…')
          return post<Appointment>(`/appointments/${appointmentId}/verify-payment`, payment)
        })
      navigate(`/dashboard?appointment=${appointmentId}${appointment.status === 'confirmed' ? '&booked=1' : ''}`)
    } catch (err) {
      const message = (err as Error).message
      if (checkoutAppointmentId) {
        navigate(`/dashboard?appointment=${checkoutAppointmentId}`, { state: { checkoutError: message } })
      } else {
        setError(message)
        if (!reservation && err instanceof ApiError && err.status === 409) {
          setSlotRevision(value => value + 1)
        }
      }
    }
    finally { setBookingStatus('') }
  }

  if (profileError) return <div className="page-shell shell">
    <h1 className="page-title">Doctor profile</h1><ErrorBox message={profileError} />
    <div className="inline-actions"><Link className="outline-button small" to={directory}>Back to doctors</Link>
      <button className="outline-button small" onClick={() => setProfileRevision(value => value + 1)}>Try again</button></div>
  </div>
  if (!doctor) return <div className="page-shell shell">
    <nav className="breadcrumbs" aria-label="Breadcrumb"><Link to={directory}>Back to doctors</Link></nav>
    <LoadingPreview label="Loading doctor profile…">
      <div className="detail-grid">
        <section className="profile-card">
          <div className="profile-heading">
            <Skeleton width="72px" height="72px" />
            <div className="skeleton-stack"><Skeleton width="36%" /><Skeleton width="74%" height="28px" /><Skeleton width="58%" /></div>
          </div>
          <div className="profile-badges"><Skeleton width="32%" /><Skeleton width="22%" /></div>
        </section>
        <section className="booking-card skeleton-stack">
          <Skeleton width="66%" height="22px" />
          <div className="slot-list"><Skeleton height="44px" /><Skeleton height="44px" /></div>
          <Skeleton width="44%" /><Skeleton height="44px" />
        </section>
      </div>
    </LoadingPreview>
  </div>
  const nextLabel = !user ? 'Sign in to book' : user.verification_required ? 'Verify account to book' : 'Continue to test payment'
  const canBook = user?.role === 'patient' || user?.role === 'admin'
  const showPrescription = (selected || reservation) && doctor.prescription_consent && canBook && !user?.verification_required
  return <div className="page-shell shell">
    <nav className="breadcrumbs" aria-label="Breadcrumb"><Link to={directory}>Back to doctors</Link><span aria-hidden="true">/</span><span aria-current="page">{doctorDisplayName(doctor.full_name)}</span></nav>
    <div className="detail-grid content-enter">
        <section className="profile-card">
          <div className="profile-heading"><Avatar name={doctor.full_name} large /><div>
            <p className="doctor-specialty">{doctor.specialty}</p>
            <h1>{doctorDisplayName(doctor.full_name)}</h1>
            <p>{doctor.qualifications} · {doctor.experience_years} years of experience</p>
            <Rating rating={doctor.rating} count={doctor.review_count} />
          </div></div>
          <div className="profile-badges"><span><BadgeCheck size={17} /> {doctor.verification_method === 'demo_seeded' ? 'Fictional demo profile' : doctor.verification_method === 'registry_matched' ? 'Registration matched' : 'Admin reviewed'}</span><span><MapPin size={17} /> {doctor.city}</span></div>
        </section>
      <section className="booking-card" aria-labelledby="booking-title">
        <h2 id="booking-title">Book an appointment</h2>
        <div className="mode-tabs" role="group" aria-label="Visit type">
          <button className={mode === 'clinic' ? 'active' : ''} aria-pressed={mode === 'clinic'} disabled={busy || !!reservation} onClick={() => choose('clinic')}><Hospital size={18} /> Clinic visit</button>
          <button className={mode === 'online' ? 'active' : ''} aria-pressed={mode === 'online'} disabled={busy || !!reservation} onClick={() => choose('online')}><Video size={18} aria-hidden="true" /> Video visit</button>
        </div>
        <div className="fee-row"><span>Consultation fee</span><strong>{fee}</strong></div>
        {slotError ? <><ErrorBox message={slotError} /><button type="button" className="outline-button small"
          onClick={() => setSlotRevision(value => value + 1)}>Reload times</button></>
          : !slots ? <LoadingPreview label="Loading available times…">
            <div className="skeleton-stack"><Skeleton height="44px" />
              <div className="slot-list">{[0, 1, 2, 3].map(index => <Skeleton key={index} height="44px" />)}</div>
            </div>
          </LoadingPreview>
          : !reservation && <>
            {!!dates.length && <label className="booking-date">Appointment date
              <select value={shownDate} disabled={busy} onChange={event => choose(mode, undefined, event.target.value)}>
                {!dates.includes(shownDate) && <option value={shownDate} disabled>{formatVisitDate(shownDate)} (unavailable)</option>}
                {dates.map(day => <option key={day} value={day}>{formatVisitDate(day)}</option>)}
              </select>
            </label>}
            <div className="slot-list-title"><b>Available times (IST)</b></div>
            {visibleSlots.length ? <div className="slot-list content-enter" key={`${mode}/${shownDate}`} role="group" aria-label="Appointment time">
              {visibleSlots.map(slot => <button type="button" disabled={busy} className="slot-option"
                aria-pressed={selected?.id === slot.id} key={slot.id} onClick={() => choose(mode, slot)}>
                <span>{visitTime.format(new Date(slot.start_at))}</span>{selected?.id === slot.id && <Check size={16} aria-hidden="true" />}
              </button>)}
            </div> : <p className="slot-empty">{dates.length ? 'No times on this date. Choose another date.' : `No ${mode === 'clinic' ? 'clinic' : 'video'} visits available. Try the other visit type.`}</p>}
          </>}
        {selectedMissing && <p className="slot-empty" role="status">That time is no longer available. Choose another.</p>}
        {(selected || reservation) && <div className="booking-summary content-enter" role="status">
          <CalendarDays size={18} aria-hidden="true" /><div><strong>{reservation ? 'Time reserved' : 'Selected time'}</strong>
            <span>{dateTime((reservation?.appointment ?? selected)!.start_at)} IST</span></div>
        </div>}
        {showPrescription && <details className="prescription-panel">
          <summary>Upload a previous prescription (optional)</summary>
          <div className="prescription-content"><PrescriptionPicker selection={prescription} onChange={setPrescription} disabled={busy} /></div>
        </details>}
        {error && <ErrorBox message={error} />}
        {user && !canBook ? <p className="booking-note">Use a patient account to book an appointment.</p> :
          <ActionButton className="button full booking-action" pending={busy} pendingLabel={bookingStatus}
            disabled={!(selected || reservation) || (!!prescription.photos.length && !prescription.consent)}
            onClick={() => void book()}>{selected || reservation ? nextLabel : 'Choose a time to continue'} <ArrowRight size={17} aria-hidden="true" /></ActionButton>}
        {showPrescription && (prescription.photos.length > 0 || reservation) && <button type="button" className="text-button prescription-skip"
          disabled={busy} onClick={() => void book(true)}>Continue without a prescription</button>}
        <p className="payment-note"><CreditCard size={16} /> Razorpay test checkout. No real charge.</p>
        <CancellationPolicy />
      </section>
      <div className="practice-details">
        <section className="content-card"><h2>Practice details</h2>{doctor.bio && <p>{doctor.bio}</p>}
          <div className="info-columns"><div><span className="info-icon"><Stethoscope size={19} /></span><div><b>Medical registration</b><small>{doctor.council} · {doctor.registration_number}</small></div></div><div><span className="info-icon"><Hospital size={19} /></span><div><b>Clinic</b><small>{doctor.clinic_name}, {doctor.clinic_address}, {doctor.city}</small></div></div></div>
        </section>
        <section className="content-card"><h2>Patient reviews</h2>{doctor.reviews?.length ? doctor.reviews.map(review => <div className="review" key={review.id}><span className="rating"><Star size={15} fill="currentColor" /> {review.rating}/5</span><span className="muted">{dateOnly(review.created_at)}</span>{review.comment && <p>{review.comment}</p>}</div>) : <p className="muted">No reviews yet.</p>}</section>
      </div>
    </div>
  </div>
}

function Meeting({ guest = false }: { guest?: boolean }) {
  const { id } = useParams()
  const [room, setRoom] = useState<{ room_name: string; domain: string } | null>(null)
  const [error, setError] = useState('')
  const [ready, setReady] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const host = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    setRoom(null)
    setError('')
    setReady(false)
    api<{ room_name: string; domain: string }>(`${guest ? '/guest' : ''}/appointments/${id}/meeting`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setRoom(value) })
      .catch(err => { if (!controller.signal.aborted) setError(err.message) })
    return () => controller.abort()
  }, [attempt, guest, id])
  useEffect(() => {
    if (!room) return
    let disposed = false
    let instance: { dispose?: () => void } | undefined
    loadScript(`https://${room.domain}/external_api.js`).then(() => {
      if (disposed) return
      const ExternalAPI = (window as unknown as { JitsiMeetExternalAPI?: new (domain: string, options: Record<string, unknown>) => { dispose?: () => void } }).JitsiMeetExternalAPI
      if (!ExternalAPI || !host.current) throw new Error('Video service unavailable')
      instance = new ExternalAPI(room.domain, { roomName: room.room_name, parentNode: host.current, width: '100%', height: '100%' })
      setReady(true)
    }).catch(() => { if (!disposed) setError('We couldn’t load the video service. Try again.') })
    return () => { disposed = true; instance?.dispose?.() }
  }, [room])
  const back = guest ? '/urgent/manage' : `/dashboard?appointment=${id}`
  return <div className="page-shell shell">
    <nav className="breadcrumbs" aria-label="Breadcrumb"><Link to={back}>{guest ? 'Guest bookings' : 'My appointments'}</Link>
      <span aria-hidden="true">/</span><span aria-current="page">Video visit</span></nav>
    <div className="page-intro"><h1>Video consultation</h1><p>Allow camera and microphone access when your browser asks.</p></div>
    {error ? <>
      <ErrorBox message={error} /><div className="inline-actions">
        <button className="outline-button small" onClick={() => setAttempt(value => value + 1)}>Try again</button>
        <Link className="outline-button small" to={back}>Back to appointments</Link>
      </div>
    </> : <div className="meeting-frame">
      {!ready && <Loading>Opening video room…</Loading>}
      <div ref={host} className="meeting-host" aria-label="Video consultation room" />
    </div>}
    <p className="meeting-note">Keep the room link private. This video room is for the demo.</p>
  </div>
}

function NotFound() { return <div className="page-shell shell"><EmptyState level={1} icon={<Search />} title="Page not found" text="The page you requested is not available." action={<Link className="button button-small" to="/">Back to home</Link>} /></div> }

export default App
