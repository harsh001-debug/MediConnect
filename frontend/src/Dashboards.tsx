import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { ArrowRight, BadgeCheck, CalendarDays, Check, Hospital, ShieldAlert, Video } from 'lucide-react'
import { ErrorBox, Field, useAuth } from './Account'
import { AppointmentCard } from './Appointments'
import { api, dateTime, doctorDisplayName, indiaDate, post } from './api'
import type { Appointment, Doctor, Slot } from './api'
import { ActionButton, AppointmentLoading, Avatar, EmptyState, Loading, LoadingPreview, Skeleton } from './ui'

function DashboardLayout({ title, subtitle, action, children }: { title: string; subtitle: string; action?: ReactNode; children: ReactNode }) {
  const { user } = useAuth()
  return <div className="dashboard-page shell">
    <div className="dashboard-intro"><div><h1>{title}</h1><p>{subtitle}</p></div>
      {action ?? (user && <div className="dashboard-user"><Avatar name={user.full_name} />
        <div><b>{user.full_name}</b><span>{user.role}</span></div></div>)}
    </div>
    {children}
  </div>
}

function useAppointments() {
  const [items, setItems] = useState<Appointment[] | null>(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    api<Appointment[]>('/appointments', { signal: controller.signal }).then(rows => {
      if (!controller.signal.aborted) setItems(rows)
    }).catch(err => {
      if (!controller.signal.aborted) setError(err.message)
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [revision])
  return { items, error, loading, refresh: () => setRevision(value => value + 1) }
}

function useElementFocus(id: string | null, visible: boolean) {
  const { key } = useLocation()
  useEffect(() => {
    if (!id || !visible) return
    const target = document.getElementById(id)
    target?.scrollIntoView({ block: 'center' })
    target?.focus({ preventScroll: true })
  }, [id, key, visible])
}

export function PatientDashboard() {
  const { user } = useAuth()
  const [params] = useSearchParams()
  const { state } = useLocation()
  const checkoutError = (state as { checkoutError?: string } | null)?.checkoutError
  const { items, error, loading, refresh } = useAppointments()
  const appointmentId = params.get('appointment')
  useElementFocus(appointmentId ? `appointment-${appointmentId}` : null, !!items?.some(item => String(item.id) === appointmentId))

  const now = Date.now()
  const active = (items ?? []).filter(item =>
    (item.status === 'confirmed' && Date.parse(item.end_at) + (item.mode === 'online' ? 30 * 60_000 : 0) > now) ||
    (item.status === 'pending_payment' && Date.parse(item.hold_expires_at) > now),
  ).sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at))
  const past = items?.filter(item => !active.includes(item)).sort((a, b) => Date.parse(b.start_at) - Date.parse(a.start_at)) ?? []
  const findDoctor = <Link className="button button-small" to="/doctors">Find a doctor <ArrowRight size={16} aria-hidden="true" /></Link>
  const cards = (appointments: Appointment[]) => <div className="appointment-grid content-enter">
    {appointments.map(item => <AppointmentCard key={item.id} item={item} role="patient" onChanged={refresh}
      checkoutError={String(item.id) === appointmentId ? checkoutError : undefined} />)}
  </div>

  return <DashboardLayout title="My appointments" subtitle={`Signed in as ${user?.full_name ?? ''}`} action={findDoctor}>
    {params.has('booked') && <p className="success-box" role="status"><Check size={17} /> Appointment confirmed.</p>}
    {error && <><ErrorBox message={error} /><button className="outline-button small" onClick={refresh}>Try again</button></>}
    {(items || loading) && <>
      <section className="dashboard-section" aria-labelledby="active-appointments-title">
        <div className="dashboard-section-head">
          <h2 id="active-appointments-title">Active appointments</h2>
          {items && loading && <Loading inline>Refreshing appointments…</Loading>}
        </div>
        {items === null ? <AppointmentLoading /> : active.length ? cards(active) :
          <EmptyState icon={<CalendarDays />} title="No active appointments" text="New bookings will appear here." />}
      </section>
      {past.length > 0 && <section className="dashboard-section" aria-labelledby="appointment-history-title">
        <h2 id="appointment-history-title">Appointment history</h2>{cards(past)}
      </section>}
    </>}
  </DashboardLayout>
}

export function DoctorDashboard() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const [profile, setProfile] = useState<Doctor | null>(null)
  const [slots, setSlots] = useState<Slot[] | null>(null)
  const [profileError, setProfileError] = useState('')
  const [slotError, setSlotError] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [pending, setPending] = useState<'add' | 'save' | number | null>(null)
  const [profileRevision, setProfileRevision] = useState(0)
  const { items, error: appointmentError, loading: appointmentsLoading, refresh: refreshAppointments } = useAppointments()
  const appointmentId = params.get('appointment')
  const tab = params.get('tab')
  const section = appointmentId || (tab !== 'slots' && tab !== 'profile') ? 'overview' : tab
  useElementFocus(appointmentId ? `appointment-${appointmentId}` : null, section === 'overview' && !!items?.some(item => String(item.id) === appointmentId))

  useEffect(() => {
    const controller = new AbortController()
    setProfileError('')
    api<Doctor>('/doctor/profile', { signal: controller.signal }).then(profile => {
      if (!controller.signal.aborted) setProfile(profile)
    }).catch(err => {
      if (!controller.signal.aborted) setProfileError(err.message)
    })
    return () => controller.abort()
  }, [profileRevision])

  const [slotRevision, setSlotRevision] = useState(0)
  const refreshSlots = () => setSlotRevision(value => value + 1)
  useEffect(() => {
    const controller = new AbortController()
    setSlotError('')
    api<Slot[]>('/doctor/slots', { signal: controller.signal }).then(setSlots).catch(err => {
      if (!controller.signal.aborted) setSlotError(err.message)
    })
    return () => controller.abort()
  }, [slotRevision])

  const upcoming = (slots ?? []).filter(slot => Date.parse(slot.start_at) > Date.now())
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at))

  const update = async (operation: 'add' | 'save' | number, action: () => Promise<void>) => {
    if (pending !== null) return
    setPending(operation)
    setError('')
    setMessage('')
    try { await action() }
    catch (err) { setError((err as Error).message) }
    finally { setPending(null) }
  }

  const addSlot = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    if (String(data.get('end_at')) <= String(data.get('start_at'))) {
      setError('Choose an end time after the start time.')
      return
    }
    void update('add', async () => {
      await post('/doctor/slots', { mode: data.get('mode'), start_at: `${data.get('start_at')}+05:30`, end_at: `${data.get('end_at')}+05:30` })
      form.reset()
      setMessage('Slot added.')
      refreshSlots()
    })
  }

  const saveProfile = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = Object.fromEntries(new FormData(event.currentTarget))
    void update('save', async () => {
      const savedProfile = await api<Doctor>('/doctor/profile', { method: 'PUT', body: JSON.stringify({ ...data,
        experience_years: Number(data.experience_years), clinic_fee_rupees: Number(data.clinic_fee_rupees), online_fee_rupees: Number(data.online_fee_rupees),
      }) })
      setProfile(current => ({ ...savedProfile, review_note: current?.review_note }))
      setMessage('Profile updated.')
    })
  }

  const today = indiaDate(new Date().toISOString())
  const retryProfile = () => setProfileRevision(value => value + 1)
  return <DashboardLayout title="My practice" subtitle={doctorDisplayName(user?.full_name ?? '')}>
    {profile && profile.verification_status !== 'published' && <div className="verification-banner"><ShieldAlert size={22} />
      <div><b>{profile.verification_status === 'rejected' ? 'Application needs attention' : 'Profile under review'}</b>
        <span>{profile.review_note || 'Patients can see your profile and book once we approve your registration.'}</span></div>
    </div>}
    <div className="dashboard-tabs" role="group" aria-label="Practice dashboard sections">
      {([['overview', 'Appointments'], ['slots', 'Availability'], ['profile', 'My profile']] as const).map(([value, label]) =>
        <button key={value} aria-pressed={section === value} className={section === value ? 'active' : ''}
          onClick={() => { setError(''); setMessage(''); setParams(value === 'overview' ? {} : { tab: value }, { replace: true }) }}>{label}</button>)}
    </div>
    {error && <ErrorBox message={error} />}
    {message && <div className="success-box" role="status"><Check size={17} /> {message}</div>}
    {section === 'overview' && <section className="dashboard-section content-enter">
      <div className="dashboard-section-head"><h2>Patient appointments</h2>
        {items && appointmentsLoading && <Loading inline>Refreshing appointments…</Loading>}
      </div>
      {appointmentError && <><ErrorBox message={appointmentError} /><button className="outline-button small" onClick={refreshAppointments}>Try again</button></>}
      {items === null ? !appointmentError && <AppointmentLoading /> : items.length ?
        <div className="appointment-grid content-enter">{items.map(item => <AppointmentCard key={item.id} item={item} role="doctor"
          onChanged={() => { refreshAppointments(); refreshSlots() }} />)}</div> :
        <EmptyState icon={<CalendarDays />} title="No appointments yet" text="Create available slots so patients can book a visit." />}
    </section>}
    {section === 'slots' && <div className="split-dashboard content-enter">
      <section className="content-card"><h2>Add availability</h2><p>Times are in India Standard Time (IST).</p>
        {profileError && <><ErrorBox message={profileError} /><button className="outline-button small" onClick={retryProfile}>Reload profile</button></>}
        {!profile && !profileError && <Loading inline>Checking profile…</Loading>}
        <form className="stack-form" onSubmit={addSlot}>
          <label>Visit type<select name="mode"><option value="clinic">Clinic visit</option><option value="online">Video consultation</option></select></label>
          <Field label="Start time" type="datetime-local" name="start_at" min={today + 'T00:00'} required />
          <Field label="End time" type="datetime-local" name="end_at" min={today + 'T00:00'} required />
          <ActionButton className="button" pending={pending === 'add'} pendingLabel="Adding time slot…"
            disabled={pending !== null || profile?.verification_status !== 'published'}>Add time slot</ActionButton>
        </form>
      </section>
      <section className="content-card"><div className="section-heading row"><h2>Upcoming slots</h2>
        {slots && <span className="muted">{upcoming.filter(slot => slot.status === 'available').length} open</span>}</div>
        {slotError ? <><ErrorBox message={slotError} /><button className="outline-button small" onClick={refreshSlots}>Try again</button></> :
          slots === null ? <LoadingPreview label="Loading slots…"><div className="skeleton-stack">
            {[0, 1, 2].map(index => <Skeleton height="52px" key={index} />)}
          </div></LoadingPreview> : upcoming.length ? <div className="manage-slots content-enter">{upcoming.map(slot =>
            <div key={slot.id}><span className="mini-icon">{slot.mode === 'clinic' ? <Hospital size={18} /> : <Video size={18} />}</span>
              <div><b>{dateTime(slot.start_at)}</b><small>{slot.mode === 'clinic' ? 'Clinic' : 'Video'} · {slot.status}</small></div>
              {slot.status === 'available' && <ActionButton type="button" className="text-button" pending={pending === slot.id}
                pendingLabel="Removing…" disabled={pending !== null} aria-label={`Remove ${dateTime(slot.start_at)} slot`} onClick={() => void update(slot.id, async () => {
                await api(`/doctor/slots/${slot.id}`, { method: 'DELETE' })
                setMessage('Slot removed.')
                refreshSlots()
              })}>Remove</ActionButton>}
            </div>)}</div> : <p className="muted">No upcoming slots.</p>}
      </section>
    </div>}
    {section === 'profile' && (profileError ? <><ErrorBox message={profileError} />
      <button className="outline-button small" onClick={retryProfile}>Reload profile</button></> : !profile ? <Loading>Loading practice profile…</Loading> :
      <section className="content-card profile-edit content-enter" id="practice-profile" tabIndex={-1}><h2>Practice profile</h2><p>Patients see these details in the doctor directory.</p>
        <div className="registration-summary"><BadgeCheck size={19} /><span>{profile.council} · {profile.registration_number} · {profile.verification_method === 'registry_matched' ? 'Registry matched' : profile.verification_status}</span></div>
        <form className="form-grid" onSubmit={saveProfile}>
          <Field label="Qualifications" name="qualifications" defaultValue={profile.qualifications} required />
          <Field label="Specialty" name="specialty" defaultValue={profile.specialty} required />
          <Field label="Years of experience" name="experience_years" type="number" min="0" max="70" defaultValue={profile.experience_years} required />
          <Field label="City" name="city" defaultValue={profile.city} required />
          <Field label="Clinic name" name="clinic_name" defaultValue={profile.clinic_name} required />
          <Field label="Clinic address" name="clinic_address" defaultValue={profile.clinic_address} required />
          <Field label="Clinic fee (₹)" name="clinic_fee_rupees" type="number" min="1" defaultValue={profile.clinic_fee_rupees} required />
          <Field label="Video consultation fee (₹)" name="online_fee_rupees" type="number" min="1" defaultValue={profile.online_fee_rupees} required />
          <label className="wide">Bio<textarea name="bio" rows={4} defaultValue={profile.bio} /></label>
          <ActionButton className="button" pending={pending === 'save'} pendingLabel="Saving profile…" disabled={pending !== null}>Save profile</ActionButton>
        </form>
      </section>)}
  </DashboardLayout>
}
