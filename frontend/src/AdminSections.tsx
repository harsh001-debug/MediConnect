import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Plus } from 'lucide-react'
import { ErrorBox, useAuth } from './Account'
import { ApiError, api, dateTime, doctorDisplayName, initials, post, rupees } from './api'
import type { CancellationQuote, Doctor, Refund, Slot } from './api'
import { adminPath, AdminDialog, AdminTable, ListFilters, ListResults, Status, useAdminData, useListing } from './AdminData'
import type { AdminAccount, Page } from './AdminData'
import type { PanelProps } from './AdminDashboard'
import { RefundReview } from './Appointments'
import { ActionButton, Loading } from './ui'

const options = (values: string[]) => values.map(value => ({
  value, label: value.charAt(0).toUpperCase() + value.slice(1).replaceAll('_', ' '),
}))

const appointmentStatuses = options(['pending_payment', 'confirmed', 'completed', 'cancelled', 'expired', 'payment_failed'])
const appointmentKinds = { label: 'Booking type', options: options(['scheduled', 'urgent']) }
const slotStatuses = options(['available', 'held', 'booked'])
const accountStatuses = options(['active', 'suspended'])
const accountRoles = { label: 'Role', options: options(['patient', 'doctor', 'admin']) }
const refundStatuses = options(['no_refund', 'queued', 'processing', 'processed', 'needs_review'])

function useAdminAction(onDone: (message: string) => void) {
  const { setUser } = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function run(action: () => Promise<string>, onError?: (error: unknown) => void) {
    if (busy) return
    setError('')
    setBusy(true)
    try {
      onDone(await action())
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setUser(null)
      setError((err as Error).message)
      onError?.(err)
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run }
}

function DialogForm({ busy, error, onClose, onSubmit, label, destructive = false, disabled = false, children }: {
  busy: boolean; error: string; onClose: () => void; onSubmit: (event: FormEvent<HTMLFormElement>) => void
  label: string; destructive?: boolean; disabled?: boolean; children?: ReactNode
}) {
  return <form className="admin-dialog-form" onSubmit={onSubmit}>
    {children}
    {error && <ErrorBox message={error} />}
    <div className="admin-dialog-actions">
      <button type="button" className="outline-button small" onClick={onClose} disabled={busy}>Back</button>
      <ActionButton className={destructive ? 'button button-small admin-danger' : 'button button-small'}
        pending={busy} pendingLabel="Saving changes…" disabled={disabled}>{label}</ActionButton>
    </div>
  </form>
}

function Reason() {
  return <label>Reason<textarea name="reason" required minLength={5} maxLength={500} rows={3} /></label>
}

function reasonFrom(form: HTMLFormElement) {
  return String(new FormData(form).get('reason') || '').trim()
}

type AdminAppointment = {
  id: number; doctor_name: string | null; patient_name: string | null; mode: string; booking_kind: string
  status: string; start_at: string; amount_paise: number; can_cancel: boolean; cancelled_by: string | null
}

export function AppointmentsPanel({ range, revision, onChanged }: PanelProps) {
  const listing = useListing()
  const [selected, setSelected] = useState<AdminAppointment | null>(null)
  const result = useAdminData<Page<AdminAppointment>>(adminPath('appointments', {
    ...range, q: listing.q, page: listing.page, status: listing.status, kind: listing.secondary,
  }), revision)
  return <section className="admin-list-panel" aria-label="Appointments">
    <p className="admin-section-note">Appointments by scheduled visit date · IST</p>
    <ListFilters listing={listing} search="Search by name, email or appointment ID" statuses={appointmentStatuses} secondary={appointmentKinds} />
    <ListResults result={result} listing={listing} empty="No appointments match these filters.">{items =>
      <AdminTable label="Appointments" headings={['Booking', 'Patient', 'Doctor', 'Visit time', 'Status', 'Fee', 'Actions']}>
        {items.map(item => <tr key={item.id}>
          <th scope="row">#{item.id}<small>{item.booking_kind} · {item.mode}</small></th>
          <td>{item.patient_name || '—'}</td><td>{item.doctor_name ? doctorDisplayName(item.doctor_name) : '—'}</td>
          <td>{dateTime(item.start_at)}</td><td><Status value={item.status} />{item.cancelled_by === 'admin' && <small>By MediConnect</small>}</td>
          <td>{rupees(item.amount_paise)}</td><td>{item.can_cancel && <button className="outline-button small danger-outline"
            aria-label={'Cancel appointment ' + item.id} onClick={() => setSelected(item)}>Cancel</button>}</td>
        </tr>)}
      </AdminTable>}</ListResults>
    {selected && <CancelDialog item={selected} onClose={() => setSelected(null)} onDone={message => {
      setSelected(null); onChanged(message)
    }} />}
  </section>
}

function CancelDialog({ item, onClose, onDone }: { item: AdminAppointment; onClose: () => void; onDone: (message: string) => void }) {
  const path = 'appointments/' + item.id
  const quote = useAdminData<CancellationQuote>(adminPath(path + '/cancellation-quote'), 0)
  const action = useAdminAction(onDone)
  return <AdminDialog title={'Cancel appointment #' + item.id} busy={action.busy} onClose={onClose}>
    <p>{item.patient_name || 'Patient'} · {dateTime(item.start_at)} IST</p>
    {quote.error && <ErrorBox message={quote.error} />}
    {quote.busy && <p role="status">Checking cancellation amount…</p>}
    {quote.data && !quote.error && <dl className="admin-quote">
      <div><dt>Captured payment</dt><dd>{rupees(quote.data.paid_paise)}</dd></div>
      <div><dt>Refund to patient</dt><dd>{rupees(quote.data.refund_paise)}</dd></div>
    </dl>}
    <DialogForm busy={action.busy} error={action.error} label="Confirm cancellation" onClose={onClose}
      disabled={quote.busy || !!quote.error || !quote.data} destructive onSubmit={event => {
        event.preventDefault()
        const reason = reasonFrom(event.currentTarget)
        const current = quote.data
        if (!current) return
        void action.run(async () => {
          await post(adminPath(path + '/cancel'), { reason, paid_paise: current.paid_paise, refund_paise: current.refund_paise })
          return 'Appointment #' + item.id + ' cancelled.' + (current.refund_paise ? ' A full refund was queued.' : '')
        }, err => { if (err instanceof ApiError && err.status === 409) quote.reload() })
      }}><Reason /></DialogForm>
  </AdminDialog>
}

type AdminSlot = Slot & { doctor_name: string | null; can_remove: boolean; doctor_suspended: boolean }

export function SlotsPanel({ range, revision, onChanged }: PanelProps) {
  const listing = useListing()
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<AdminSlot | null>(null)
  const result = useAdminData<Page<AdminSlot>>(adminPath('slots', {
    ...range, q: listing.q, page: listing.page, status: listing.status,
  }), revision)
  return <section className="admin-list-panel" aria-label="Slots">
    <p className="admin-section-note">Doctor availability by visit date · IST</p>
    <ListFilters listing={listing} search="Search by doctor name or email" statuses={slotStatuses}>
      <button className="button button-small admin-filter-action" onClick={() => setAdding(true)}><Plus size={18} /> Add slot</button>
    </ListFilters>
    <ListResults result={result} listing={listing} empty="No slots match these filters.">{items =>
      <AdminTable label="Slots" headings={['Doctor', 'Starts', 'Ends', 'Type', 'Status', 'Actions']}>
        {items.map(slot => <tr key={slot.id}>
          <th scope="row">{slot.doctor_name ? doctorDisplayName(slot.doctor_name) : '—'}
            {slot.doctor_suspended && <small>Doctor suspended</small>}</th>
          <td>{dateTime(slot.start_at)}</td><td>{dateTime(slot.end_at)}</td><td className="admin-capitalize">{slot.mode}</td>
          <td><Status value={slot.status} /></td>
          <td>{slot.can_remove && <button className="outline-button small danger-outline"
            aria-label={'Remove slot for ' + (slot.doctor_name || 'doctor') + ' at ' + dateTime(slot.start_at)}
            onClick={() => setRemoving(slot)}>Remove</button>}</td>
        </tr>)}
      </AdminTable>}</ListResults>
    {adding && <AddSlotDialog onClose={() => setAdding(false)} onDone={message => { setAdding(false); onChanged(message) }} />}
    {removing && <RemoveSlotDialog slot={removing} onClose={() => setRemoving(null)}
      onDone={message => { setRemoving(null); onChanged(message) }} />}
  </section>
}

function AddSlotDialog({ onClose, onDone }: { onClose: () => void; onDone: (message: string) => void }) {
  const [draft, setDraft] = useState('')
  const [search, setSearch] = useState('')
  const [doctor, setDoctor] = useState('')
  const result = useAdminData<Page<AdminAccount>>(adminPath('accounts', { role: 'doctor', eligible: 'true', q: search, limit: 50 }), 0)
  const action = useAdminAction(onDone)
  return <AdminDialog title="Add a slot" busy={action.busy} onClose={onClose}>
    <form className="admin-search admin-slot-search" onSubmit={event => {
      event.preventDefault()
      if (draft.trim() !== search) {
        setDoctor('')
        setSearch(draft.trim())
      }
    }}>
      <label>Find a published doctor<input type="search" value={draft} maxLength={120} onChange={event => setDraft(event.target.value)} /></label>
      <button className="outline-button small">Find</button>
    </form>
    {result.error && <ErrorBox message={result.error} />}
    {result.data && result.data.total > result.data.items.length && <p>Search by name to narrow the doctor list.</p>}
    <DialogForm busy={action.busy} error={action.error} label="Add slot" onClose={onClose} disabled={!doctor || !!result.error}
      onSubmit={event => {
        event.preventDefault()
        const fields = new FormData(event.currentTarget)
        void action.run(async () => {
          const slot = await post<Slot>(adminPath('doctors/' + doctor + '/slots'), {
            mode: fields.get('mode'), start_at: String(fields.get('start_at')) + '+05:30', end_at: String(fields.get('end_at')) + '+05:30',
          })
          return 'Slot added for ' + dateTime(slot.start_at) + ' IST.'
        })
      }}>
      <label>Doctor<select value={doctor} required onChange={event => setDoctor(event.target.value)}>
        <option value="">{result.busy ? 'Loading doctors…' : !result.data?.items.length ? 'No eligible doctors found' : 'Select a doctor'}</option>
        {result.data?.items.map(item => <option value={item.id} key={item.id}>
          {doctorDisplayName(item.full_name)} · {item.specialty}
        </option>)}
      </select></label>
      <label>Consultation type<select name="mode"><option value="clinic">Clinic visit</option><option value="online">Online consultation</option></select></label>
      <div className="admin-form-columns">
        <label>Starts (IST)<input name="start_at" type="datetime-local" required /></label>
        <label>Ends (IST)<input name="end_at" type="datetime-local" required /></label>
      </div>
    </DialogForm>
  </AdminDialog>
}

function RemoveSlotDialog({ slot, onClose, onDone }: { slot: AdminSlot; onClose: () => void; onDone: (message: string) => void }) {
  const action = useAdminAction(onDone)
  return <AdminDialog title="Remove availability" busy={action.busy} onClose={onClose}>
    <p>{slot.doctor_name && doctorDisplayName(slot.doctor_name)} · {dateTime(slot.start_at)} IST</p>
    <DialogForm busy={action.busy} error={action.error} label="Remove slot" onClose={onClose} destructive onSubmit={event => {
      event.preventDefault()
      void action.run(async () => {
        await api(adminPath('slots/' + slot.id), { method: 'DELETE' })
        return 'Slot removed.'
      })
    }} />
  </AdminDialog>
}

export function AccountsPanel({ revision, onChanged }: PanelProps) {
  const listing = useListing()
  const [selected, setSelected] = useState<AdminAccount | null>(null)
  const [promoting, setPromoting] = useState<AdminAccount | null>(null)
  const result = useAdminData<Page<AdminAccount>>(adminPath('accounts', {
    q: listing.q, page: listing.page, status: listing.status, role: listing.secondary,
  }), revision)
  return <section className="admin-list-panel" aria-label="Accounts">
    <ListFilters listing={listing} search="Search by name or email" statuses={accountStatuses} secondary={accountRoles} />
    <ListResults result={result} listing={listing} empty="No accounts match these filters.">{items =>
      <AdminTable label="Accounts" headings={['Account', 'Role', 'Status', 'Registered', 'Actions']}>
        {items.map(account => <tr key={account.id}>
          <th scope="row">{account.full_name}<small>{account.email}</small></th>
          <td className="admin-capitalize">{account.role}{account.doctor_status && <small>{account.doctor_status} profile</small>}</td>
          <td><Status value={account.suspended_at ? 'suspended' : 'active'} /></td>
          <td>{dateTime(account.created_at)}</td>
          <td>{account.role !== 'admin' && <div className="admin-account-actions">
            {!account.suspended_at && <button className="outline-button small" onClick={() => setPromoting(account)}>Make admin</button>}
            <button className={'outline-button small' + (account.suspended_at ? '' : ' danger-outline')}
              onClick={() => setSelected(account)}>{account.suspended_at ? 'Reactivate' : 'Suspend'}</button>
          </div>}</td>
        </tr>)}
      </AdminTable>}</ListResults>
    {selected && <AccountDialog account={selected} onClose={() => setSelected(null)}
      onDone={message => { setSelected(null); onChanged(message) }} />}
    {promoting && <MakeAdminDialog account={promoting} onClose={() => setPromoting(null)}
      onDone={message => { setPromoting(null); onChanged(message) }} />}
  </section>
}

function MakeAdminDialog({ account, onClose, onDone }: { account: AdminAccount; onClose: () => void; onDone: (message: string) => void }) {
  const action = useAdminAction(onDone)
  return <AdminDialog title={'Make ' + account.full_name + ' an admin'} busy={action.busy} onClose={onClose}>
    <p>{account.email} will gain access to account management, appointments, doctor reviews, and financial reports.
      They will need to sign in again.</p>
    <DialogForm busy={action.busy} error={action.error} label="Confirm admin access" onClose={onClose}
      onSubmit={event => {
        event.preventDefault()
        const reason = reasonFrom(event.currentTarget)
        void action.run(async () => {
          await post(adminPath('accounts/' + account.id + '/make-admin'), { reason })
          return account.full_name + ' now has administrator access.'
        })
      }}><Reason /></DialogForm>
  </AdminDialog>
}

function AccountDialog({ account, onClose, onDone }: { account: AdminAccount; onClose: () => void; onDone: (message: string) => void }) {
  const suspend = !account.suspended_at
  const verb = suspend ? 'Suspend' : 'Reactivate'
  const action = useAdminAction(onDone)
  return <AdminDialog title={verb + ' ' + account.full_name} busy={action.busy} onClose={onClose}>
    <p>{suspend ? 'This revokes sign-in, cancels upcoming bookings and active holds, and queues full refunds for captured payments.'
      : 'This restores account access. The user can sign in again.'}</p>
    <DialogForm busy={action.busy} error={action.error} label={verb + ' account'} onClose={onClose} destructive={suspend}
      onSubmit={event => {
        event.preventDefault()
        const reason = reasonFrom(event.currentTarget)
        void action.run(async () => {
          const result = await api<{ cancelled_appointments: number }>(adminPath('accounts/' + account.id + '/status'), {
            method: 'PUT', body: JSON.stringify({ suspended: suspend, reason }),
          })
          if (!suspend) return account.full_name + ' was reactivated.'
          const count = result.cancelled_appointments
          return account.full_name + ' was suspended. ' + count + (count === 1 ? ' appointment or hold cancelled.' : ' appointments or holds cancelled.')
        })
      }}><Reason /></DialogForm>
  </AdminDialog>
}

type PaymentRow = {
  appointment_id: number; captured_at: string; amount_paise: number; payment_id: string | null; order_id: string | null
  late_capture: boolean; refund_amount_paise: number; refund_status: Refund['status'] | null; refund_reference: string | null
}

export function TransactionsPanel({ range, revision }: PanelProps) {
  const listing = useListing()
  const result = useAdminData<Page<PaymentRow>>(adminPath('transactions', {
    ...range, q: listing.q, page: listing.page, status: listing.status,
  }), revision)
  return <>
    <section className="admin-list-panel" aria-label="Transactions">
      <p className="admin-section-note">Razorpay test payments by capture date · IST</p>
      <ListFilters listing={listing} search="Search by appointment or provider reference" statuses={refundStatuses} />
      <ListResults result={result} listing={listing} empty="No captured transactions match these filters.">{items =>
        <AdminTable label="Transactions" headings={['Appointment', 'Captured', 'Amount', 'Refund', 'Provider references']}>
          {items.map(payment => <tr key={payment.payment_id ?? payment.appointment_id}>
            <th scope="row">#{payment.appointment_id}{payment.late_capture && <small>Late or extra capture</small>}</th>
            <td>{dateTime(payment.captured_at)}</td><td>{rupees(payment.amount_paise)}</td>
            <td>{payment.refund_status ? <><Status value={payment.refund_status} />
              <small>{rupees(payment.refund_amount_paise)}</small></> : '—'}</td>
            <td className="admin-reference">
              {payment.payment_id && <span>{payment.payment_id}</span>}
              {payment.order_id && <small>{payment.order_id}</small>}
              {payment.refund_reference && <small>{payment.refund_reference}</small>}
            </td>
          </tr>)}
        </AdminTable>}</ListResults>
    </section>
    <RefundReview />
  </>
}

export function DoctorReviewsPanel({ revision, onChanged }: PanelProps) {
  const result = useAdminData<Doctor[]>(adminPath('pending-doctors'), revision)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const active = (result.data ?? []).filter(doctor =>
    [doctor.full_name, doctor.registration_number, doctor.city].some(value => value.toLowerCase().includes(query.trim().toLowerCase())))
  const pages = Math.max(1, Math.ceil(active.length / 8))
  const currentPage = Math.min(page, pages)
  return <section className="admin-list-panel" aria-label="Pending doctor reviews">
    <div className="admin-filters">
      <label>Search by name, registration or city<input type="search" value={query} maxLength={120} onChange={event => {
        setQuery(event.target.value); setPage(1)
      }} /></label>
    </div>
    {result.error && <div className="admin-fetch-error"><ErrorBox message={result.error} />
      <button className="outline-button small" onClick={result.reload}>Try again</button></div>}
    {!result.data && !result.error && <Loading>Loading applications…</Loading>}
    {result.data && (active.length ? <div className="admin-grid">{active.slice((currentPage - 1) * 8, currentPage * 8).map(doctor =>
      <ReviewCard key={doctor.id} doctor={doctor} onDone={onChanged} />)}</div> :
      <p className="admin-empty">{query ? 'No applications match this search.' : 'No pending applications.'}</p>)}
    {pages > 1 && <div className="admin-pagination">
      <span>{active.length} applications · Page {currentPage} of {pages}</span>
      <div><button className="outline-button small" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>Previous</button>
        <button className="outline-button small" disabled={currentPage === pages} onClick={() => setPage(currentPage + 1)}>Next</button></div>
    </div>}
  </section>
}

function ReviewCard({ doctor, onDone }: { doctor: Doctor; onDone: (message: string) => void }) {
  const action = useAdminAction(onDone)
  return <div className="content-card">
    <div className="admin-doctor"><span className="avatar">{initials(doctor.full_name)}</span>
      <div><h2>{doctorDisplayName(doctor.full_name)}</h2><p>{doctor.specialty} · {doctor.city}</p></div></div>
    <div className="admin-details">
      <span><b>Council</b>{doctor.council}</span><span><b>Registration no.</b>{doctor.registration_number}</span>
      <span><b>Registration date</b>{doctor.registration_date}</span><span><b>Qualification</b>{doctor.qualifications}</span>
    </div>
    <div className="check-result">
      <b>{doctor.check ? 'Automated lookup · ' + doctor.check.result : 'Online lookup skipped'}</b>
      <p>{doctor.check?.detail || 'The applicant submitted without confirming an online match.'}</p>
      <a href={doctor.check?.source_url || 'https://nmc.org.in/information-desk/indian-medical-register'}
        target="_blank" rel="noreferrer">Open official NMC register</a>
    </div>
    <form onSubmit={event => {
      event.preventDefault()
      const note = String(new FormData(event.currentTarget).get('note') || '').trim()
      const decision = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value')
      if (decision !== 'approved' && decision !== 'rejected') return
      void action.run(async () => {
        await post(adminPath('doctors/' + doctor.id + '/decision'), { decision, note })
        return doctorDisplayName(doctor.full_name) + '’s application was ' + decision + '.'
      })
    }}>
      <label>Review note<textarea name="note" required minLength={5} maxLength={500} rows={3} /></label>
      {action.error && <ErrorBox message={action.error} />}
      <div className="admin-actions">
        <button className="button button-small" value="approved" disabled={action.busy}>Approve profile</button>
        <button className="outline-button small danger-outline" value="rejected" disabled={action.busy}>Reject</button>
      </div>
    </form>
  </div>
}
