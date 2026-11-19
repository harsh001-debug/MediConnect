import { createContext, useContext, useEffect, useId, useRef, useState } from 'react'
import type { FormEvent, InputHTMLAttributes, ReactNode } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { ArrowRight, Check, Eye, EyeOff, Mail, Smartphone } from 'lucide-react'
import { api, ApiError, post } from './api'
import type { ContactChannel, DoctorRegistration, RegistryLookup, User } from './api'
import { ActionButton } from './ui'

type AuthState = { user: User | null; setUser: (user: User | null) => void }
export const AuthContext = createContext<AuthState>({ user: null, setUser: () => {} })
export const useAuth = () => useContext(AuthContext)

export function accountPath(page: 'login' | 'signup' | 'verify-contacts' | 'forgot-password', next = '') {
  return `/${page}${next ? `?${new URLSearchParams({ next })}` : ''}`
}

function useReturnPath() {
  const [params] = useSearchParams()
  const next = params.get('next') ?? ''
  return /^\/(?:doctors\/\d+|meeting\/\d+|dashboard|admin(?:\/(?:appointments|slots|accounts|doctors|transactions))?)(?:\?|$)/.test(next) ? next : ''
}

function accountDestination(user: User, next: string) {
  if (user.role === 'admin') return next || '/admin'
  const destination = /^\/admin(?:[/?]|$)/.test(next) ? '/dashboard' : next || '/dashboard'
  return user.verification_required ? accountPath('verify-contacts', destination) : destination
}

function AccountLayout({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return <div className="account-stage"><div className="account-panel">
    <h1>{title}</h1>
    {description && <p>{description}</p>}
    {children}
  </div></div>
}

export function Login() {
  const { setUser, user } = useAuth()
  const next = useReturnPath()
  const { state } = useLocation()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  if (user) return <Navigate to={accountDestination(user, next)} state={state} replace />

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    const data = Object.fromEntries(new FormData(event.currentTarget))
    try { setUser(await post<User>('/auth/login', data)) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  return <AccountLayout title="Sign in" description={next.startsWith('/doctors/')
    ? 'Use your patient account to keep your selected appointment time.' : undefined}>
    <form className="account-form" onSubmit={submit}>
      <Field label="Email address" name="email" type="email" autoComplete="username" required />
      <PasswordField />
      <Link className="account-recovery-link" to={accountPath('forgot-password', next)} state={state}>Forgot password?</Link>
      {error && <ErrorBox message={error} />}
      <ActionButton className="button full" pending={busy} pendingLabel="Signing in…">Sign in <ArrowRight size={17} aria-hidden="true" /></ActionButton>
    </form>
    <p className="auth-switch">New to MediConnect? <Link to={accountPath('signup', next)} state={state}>Create an account</Link></p>
    <section className="guest-entry" aria-labelledby="guest-entry-title">
      <h2 id="guest-entry-title">Guest appointments</h2>
      <Link className="outline-button full" to="/urgent">Book an urgent appointment <ArrowRight size={17} /></Link>
      <Link className="text-button" to="/urgent/manage">Manage a guest booking</Link>
    </section>
  </AccountLayout>
}

function PasswordField({ creating = false, label = 'Password' }: { creating?: boolean; label?: string }) {
  const [visible, setVisible] = useState(false)
  const id = useId()
  return <div>
    <div className="password-field">
      <label htmlFor={id}>{label}
        <input id={id} name="password" type={visible ? 'text' : 'password'} required maxLength={128}
          minLength={creating ? 8 : undefined} autoComplete={creating ? 'new-password' : 'current-password'}
          aria-describedby={creating ? id + '-hint' : undefined} />
      </label>
      <button type="button" className="text-button password-toggle" aria-controls={id}
        aria-label={visible ? 'Hide password' : 'Show password'} onClick={() => setVisible(value => !value)}>
        {visible ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
      </button>
    </div>
    {creating && <small className="field-hint" id={id + '-hint'}>Use at least 8 characters.</small>}
  </div>
}

export function ForgotPassword() {
  const { user } = useAuth()
  const { state } = useLocation()
  const next = useReturnPath()
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [step, setStep] = useState<'email' | 'reset' | 'done'>('email')
  const [pending, setPending] = useState<'send' | 'reset' | null>(null)
  const [error, setError] = useState('')
  const [resendAt, setResendAt] = useState(0)
  const [wait, setWait] = useState(0)

  useEffect(() => {
    if (step !== 'reset') return
    const update = () => setWait(Math.max(0, Math.ceil((resendAt - Date.now()) / 1000)))
    update()
    const timer = window.setInterval(update, 1000)
    return () => window.clearInterval(timer)
  }, [resendAt, step])

  if (user) return <Navigate to={accountDestination(user, next)} state={state} replace />

  const requestCode = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault()
    setPending('send')
    setError('')
    try {
      await post('/auth/password-reset/request', { email: email.trim() })
      setResendAt(Date.now() + 60_000)
      setWait(60)
      setCode('')
      setStep('reset')
    } catch (err) { setError((err as Error).message) }
    finally { setPending(null) }
  }

  const reset = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const fields = new FormData(event.currentTarget)
    const password = fields.get('password')
    setError('')
    if (password !== fields.get('confirmation')) {
      setError('The passwords don’t match.')
      return
    }
    setPending('reset')
    try {
      await post('/auth/password-reset/confirm', { email: email.trim(), code, password })
      setCode('')
      setStep('done')
    } catch (err) { setError((err as Error).message) }
    finally { setPending(null) }
  }

  const signIn = <Link to={accountPath('login', next)} state={state}>Back to sign in</Link>
  if (step === 'done') return <AccountLayout title="Password updated">
    <p role="status">Sign in again with your new password.</p>
    <Link className="button full" to={accountPath('login', next)} state={state} replace>Sign in</Link>
  </AccountLayout>

  return <AccountLayout title="Reset your password" description={step === 'email'
    ? 'Enter the verified email address on your account.'
    : 'If this address matches a verified account, check your inbox for a six-digit code. The code expires in 10 minutes.'}>
    {step === 'email' ? <form className="account-form" onSubmit={requestCode}>
      <Field label="Email address" name="email" type="email" autoComplete="email" maxLength={320}
        value={email} onChange={event => setEmail(event.target.value)} disabled={pending !== null} required />
      {error && <ErrorBox message={error} />}
      <ActionButton className="button full" pending={pending === 'send'} pendingLabel="Requesting code…">Send email code</ActionButton>
    </form> : <>
      <div className="contact-heading"><div><h2>Email address</h2><p>{email.trim()}</p></div>
        <button type="button" className="text-button" disabled={pending !== null} onClick={() => {
          setError('')
          setCode('')
          setStep('email')
        }}>Change</button>
      </div>
      <form className="account-form" onSubmit={reset}>
        <Field label="6-digit email code" name="code" value={code} inputMode="numeric" autoComplete="one-time-code" autoFocus
          onChange={event => setCode(event.target.value.replace(/[^0-9]/g, '').slice(0, 6))}
          pattern="[0-9]{6}" maxLength={6} required />
        <PasswordField creating label="New password" />
        <Field label="Confirm new password" name="confirmation" type="password" autoComplete="new-password"
          minLength={8} maxLength={128} required />
        {error && <ErrorBox message={error} />}
        <ActionButton className="button full" pending={pending === 'reset'} pendingLabel="Updating password…"
          disabled={pending !== null || code.length !== 6}>Reset password</ActionButton>
        <ActionButton type="button" className="text-button resend-button" pending={pending === 'send'} pendingLabel="Requesting code…"
          disabled={pending !== null || wait > 0} onClick={() => void requestCode()}>{wait ? `Resend code in ${wait}s` : 'Resend code'}</ActionButton>
      </form>
    </>}
    <p className="auth-switch">{signIn}</p>
  </AccountLayout>
}

const registrationFields = { full_name: '', council: '', registration_number: '' }

type RegistrationSearch =
  | { state: 'searching' }
  | { state: 'error'; message: string }
  | { state: 'result'; details: DoctorRegistration; lookup: RegistryLookup; confirmed: boolean }

export function Signup() {
  const [params, setParams] = useSearchParams()
  const { state } = useLocation()
  const role = params.get('role') === 'doctor' ? 'doctor' : 'patient'
  const { setUser, user } = useAuth()
  const next = useReturnPath()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [registration, setRegistration] = useState(registrationFields)
  const [search, setSearch] = useState<RegistrationSearch | null>(null)
  const searchVersion = useRef(0)
  if (user) return <Navigate to={accountDestination(user, next)} state={state} replace />

  const searchReady = registration.full_name.trim().length >= 2
    && registration.council.trim().length >= 3 && /[a-z0-9]/i.test(registration.council)
    && registration.registration_number.trim().length >= 3 && /[a-z0-9]/i.test(registration.registration_number)
  const matchedRecord = search?.state === 'result' && search.lookup.status === 'matched' ? search.lookup.record : null

  const clearSearch = () => {
    searchVersion.current += 1
    setSearch(null)
  }
  const changeRegistration = (field: keyof DoctorRegistration, value: string) => {
    setRegistration(current => ({ ...current, [field]: value }))
    clearSearch()
  }
  const searchRegistry = async () => {
    const version = ++searchVersion.current
    const details = registration
    setSearch({ state: 'searching' })
    try {
      const lookup = await post<RegistryLookup>('/auth/doctor-registration-lookup', details)
      if (version === searchVersion.current) setSearch({ state: 'result', details, lookup, confirmed: false })
    } catch (err) {
      if (version === searchVersion.current) setSearch({ state: 'error', message: (err as Error).message })
    }
  }
  const switchRole = (value: string) => {
    if (value === role) return
    const updated = new URLSearchParams(params)
    updated.set('role', value)
    clearSearch()
    setRegistration(current => ({ ...registrationFields, full_name: current.full_name }))
    setError('')
    setParams(updated, { replace: true, state })
  }
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    const data = Object.fromEntries(new FormData(event.currentTarget))
    const confirmRegistryMatch = search?.state === 'result' && search.confirmed
      && Object.entries(search.details).every(([key, value]) => data[key] === value)
    const payload = role === 'doctor' ? { ...data, confirm_registry_match: confirmRegistryMatch } : data
    try { setUser(await post<User>(`/auth/${role}-signup`, payload)) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  return <div className="signup-stage"><div className="signup-shell shell">
    <div className="page-intro"><h1>{role === 'doctor' ? 'Register as a doctor' : 'Create a patient account'}</h1>
      <p>After you submit, verify your email and mobile number.</p></div>
    <div className="signup-tabs" role="group" aria-label="Account type">
      <button type="button" aria-pressed={role === 'patient'} className={role === 'patient' ? 'active' : ''} disabled={busy} onClick={() => switchRole('patient')}>Patient</button>
      <button type="button" aria-pressed={role === 'doctor'} className={role === 'doctor' ? 'active' : ''} disabled={busy} onClick={() => switchRole('doctor')}>Doctor</button>
    </div>
    <form className="signup-card" onSubmit={submit}>
      <div className="form-section">
        <h2>Account details</h2>
        <div className="form-grid">
          <Field label="Full name" name="full_name" autoComplete="name" maxLength={120} required
            value={registration.full_name} onChange={event => changeRegistration('full_name', event.target.value)} />
          <Field label="Email address" name="email" type="email" autoComplete="email" maxLength={320} required />
          <Field label="Mobile number" name="phone" type="tel" autoComplete="tel" maxLength={32} placeholder="+91 98765 43210" required />
          <PasswordField creating />
        </div>
      </div>
      {role === 'doctor' && <>
        <div className="form-section">
          <h2>Medical registration</h2>
          <p>Check your name, council and number in the NMC register, or submit for manual review.</p>
          <div className="form-grid">
            <Field label="State medical council" name="council" minLength={3} maxLength={120} required
              value={registration.council} onChange={event => changeRegistration('council', event.target.value)} />
            <Field label="Registration number" name="registration_number" minLength={3} maxLength={80} required
              value={registration.registration_number} onChange={event => changeRegistration('registration_number', event.target.value)} />
            <Field label="Registration date" name="registration_date" type="date" required />
            <Field label="Qualifications" name="qualifications" placeholder="e.g. MBBS, MD" maxLength={300} required />
            <Field label="Specialty" name="specialty" placeholder="e.g. General Medicine" maxLength={100} required />
            <Field label="Years of experience" name="experience_years" type="number" min="0" max="70" required />
          </div>
          <div className="registration-search">
            <ActionButton type="button" className="outline-button small" onClick={() => void searchRegistry()}
              pending={search?.state === 'searching'} pendingLabel="Searching the register…" disabled={!searchReady || busy}>
              Search NMC register
            </ActionButton>
            <div aria-live="polite">
              {search?.state === 'error' && <ErrorBox message={search.message} />}
              {search?.state === 'result' && <div className="registration-result">
                {matchedRecord ? <>
                  <b>Matching registration found</b>
                  <dl>
                    <div><dt>Registered name</dt><dd>{matchedRecord.full_name}</dd></div>
                    <div><dt>Registration number</dt><dd>{matchedRecord.registration_number}</dd></div>
                    <div><dt>Issuing council</dt><dd>{matchedRecord.council}</dd></div>
                    {matchedRecord.qualifications && <div><dt>Qualifications listed by NMC</dt><dd>{matchedRecord.qualifications}</dd></div>}
                  </dl>
                </> : <p>{search.lookup.status === 'review'
                  ? 'No unique exact match was found. Check your details or continue for manual review.'
                  : 'The NMC lookup is currently unavailable. Try again or continue for manual review.'}</p>}
                <a href={search.lookup.source_url} target="_blank" rel="noreferrer">Open official NMC register</a>
              </div>}
            </div>
            {matchedRecord && search?.state === 'result' && (search.confirmed
              ? <p role="status">Registration match confirmed. MediConnect will recheck it when you submit.</p>
              : <button type="button" className="button button-small" disabled={busy}
                  onClick={() => setSearch({ ...search, confirmed: true })}>Verify this registration</button>)}
          </div>
        </div>
        <div className="form-section">
          <h2>Practice details</h2>
          <div className="form-grid">
            <Field label="Clinic name" name="clinic_name" maxLength={120} required />
            <Field label="City" name="city" autoComplete="address-level2" maxLength={80} required />
            <Field label="Clinic address" name="clinic_address" autoComplete="street-address" maxLength={300} required wide />
            <Field label="Clinic visit fee (₹)" name="clinic_fee_rupees" type="number" min="1" max="100000" required />
            <Field label="Online consultation fee (₹)" name="online_fee_rupees" type="number" min="1" max="100000" required />
            <label className="wide">Short bio (optional)<textarea name="bio" rows={4} maxLength={1000} /></label>
          </div>
        </div>
      </>}
      {error && <ErrorBox message={error} />}
      <ActionButton className="button" pending={busy} pendingLabel="Creating account…">Continue to verification <ArrowRight size={17} aria-hidden="true" /></ActionButton>
      <p className="auth-switch">Already have an account? <Link to={accountPath('login', next)} state={state}>Sign in</Link></p>
    </form>
  </div></div>
}

const contactLabels = { email: 'Email address', phone: 'Mobile number' }

export function VerifyContacts() {
  const { user } = useAuth()
  const next = useReturnPath()
  const { state } = useLocation()
  if (!user) return <Navigate to={accountPath('login', next)} state={state} replace />
  if (!user.verification_required) return <Navigate to={accountDestination(user, next)} state={state} replace />

  const channel = user.contacts.email.verified ? 'phone' : 'email'
  return <AccountLayout title="Verify your contact details" description="Confirm your email and mobile number to finish setting up your account.">
    <ol className="contact-progress" aria-label="Verification progress">
      {(['email', 'phone'] as const).map(item => <li key={item} aria-current={channel === item ? 'step' : undefined}>
        {user.contacts[item].verified ? <Check size={17} /> : item === 'email' ? <Mail size={17} /> : <Smartphone size={17} />}
        <span>{contactLabels[item]}{user.contacts[item].verified && <span className="visually-hidden"> verified</span>}</span>
      </li>)}
    </ol>
    <ContactStep key={channel} user={user} channel={channel} />
  </AccountLayout>
}

function ContactStep({ user, channel }: { user: User; channel: ContactChannel }) {
  const { setUser } = useAuth()
  const status = user.contacts[channel]
  const [editing, setEditing] = useState(false)
  const [pendingAction, setPendingAction] = useState<'send' | 'verify' | 'edit' | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [now, setNow] = useState(Date.now)
  const codeField = useRef<HTMLInputElement>(null)
  const validCode = !!status.expires_at && Date.parse(status.expires_at) > now
  const wait = status.resend_at ? Math.max(0, Math.ceil((Date.parse(status.resend_at) - now) / 1000)) : 0
  const endpoint = `/auth/contacts/${channel}`
  const transport = channel === 'email' ? 'email' : 'SMS'
  const busy = pendingAction !== null
  useEffect(() => {
    if (!status.resend_at && !status.expires_at) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [status.resend_at, status.expires_at])
  useEffect(() => { if (validCode && !editing) codeField.current?.focus() }, [validCode, editing])

  const submit = async (action: 'send' | 'verify' | 'edit', event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault()
    const fields = event ? Object.fromEntries(new FormData(event.currentTarget)) : undefined
    setPendingAction(action)
    setError('')
    setNotice('')
    try {
      const updated = action === 'edit'
        ? await api<User>(endpoint, { method: 'PUT', body: JSON.stringify(fields) })
        : await post<User>(`${endpoint}/${action}`, fields)
      setNow(Date.now())
      setUser(updated)
      if (action === 'send') {
        setCode('')
        setNotice(`Check your ${transport} for the code. It expires in 10 minutes.`)
        codeField.current?.focus()
      } else if (action === 'edit') {
        setEditing(false)
        setCode('')
        setNotice(`${contactLabels[channel]} updated.`)
      } else if (channel === 'email') {
        requestAnimationFrame(() => document.getElementById('contact-step-title')?.focus())
      }
    } catch (err) {
      setError((err as Error).message)
      if (err instanceof ApiError && (err.status === 409 || (action === 'verify' && err.status === 429)
        || (action === 'send' && err.status === 503))) {
        try { setUser(await api<User>('/auth/me')) } catch { /* Keep the actionable error visible. */ }
      }
    } finally { setPendingAction(null) }
  }

  return <section className="contact-step" aria-labelledby="contact-step-title">
    <div className="contact-heading"><div><h2 id="contact-step-title" tabIndex={-1}>{contactLabels[channel]}</h2><p>{user[channel]}</p></div>
      <button type="button" className="text-button" disabled={busy} onClick={() => { setEditing(!editing); setError(''); setNotice('') }}>{editing ? 'Cancel' : 'Change'}</button>
    </div>
    {notice && <p className="contact-notice" role="status">{notice}</p>}
    {error && <ErrorBox message={error} />}
    {editing ? <form className="account-form" onSubmit={event => void submit('edit', event)}>
      <Field label={`New ${contactLabels[channel].toLowerCase()}`} name="value" defaultValue={user[channel]} type={channel === 'email' ? 'email' : 'tel'} maxLength={channel === 'email' ? 320 : 32} required />
      <Field label="Current password" name="password" type="password" autoComplete="current-password" maxLength={128} required />
      <ActionButton className="button full" pending={pendingAction === 'edit'} pendingLabel="Saving changes…">Save changes</ActionButton>
    </form> : validCode ? <form className="account-form" onSubmit={event => void submit('verify', event)}>
      <label>6-digit code<input ref={codeField} name="code" value={code} onChange={event => setCode(event.target.value.replace(/[^0-9]/g, '').slice(0, 6))} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required /></label>
      <ActionButton className="button full" pending={pendingAction === 'verify'} pendingLabel="Checking code…" disabled={busy || code.length !== 6}>
        Verify {channel === 'email' ? 'email' : 'mobile number'}
      </ActionButton>
      <ActionButton type="button" className="text-button resend-button" pending={pendingAction === 'send'} pendingLabel="Sending code…"
        disabled={busy || wait > 0} onClick={() => void submit('send')}>{wait ? `Resend code in ${wait}s` : 'Resend code'}</ActionButton>
    </form> : <div className="account-form">
      <p>{status.resend_at ? 'Request a new code to continue.' : `We’ll send a 6-digit code by ${transport}.`}</p>
      <ActionButton type="button" className="button full" pending={pendingAction === 'send'} pendingLabel="Sending code…"
        disabled={busy || wait > 0} onClick={() => void submit('send')}>{wait ? `Send code in ${wait}s` : `Send ${transport} code`}</ActionButton>
    </div>}
  </section>
}

export function Field({ label, name, wide, ...props }: { label: string; name: string; wide?: boolean } & InputHTMLAttributes<HTMLInputElement>) {
  return <label className={wide ? 'wide' : ''}>{label}<input name={name} {...props} /></label>
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="error-box" role="alert">{message}</div>
}
