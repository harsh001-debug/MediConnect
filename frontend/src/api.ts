export type Role = 'patient' | 'doctor' | 'admin'
export type ContactChannel = 'email' | 'phone'
type ContactStatus = { verified: boolean; expires_at: string | null; resend_at: string | null }
export type User = { id: number; email: string; full_name: string; phone: string; role: Role; verification_status: string | null; verification_required: boolean; contacts: Record<ContactChannel, ContactStatus> }
export type DoctorRegistration = { full_name: string; council: string; registration_number: string }
export type RegistryLookup = {
  status: 'matched' | 'review' | 'unavailable'
  source_url: string
  record: (DoctorRegistration & { qualifications: string | null }) | null
}
export type VisitMode = 'clinic' | 'online'
export type Doctor = { id: number; full_name: string; council: string; registration_number: string; registration_date?: string; qualifications: string; specialty: string; experience_years: number; clinic_name: string; clinic_address: string; city: string; bio: string; clinic_fee_rupees: number; online_fee_rupees: number; verification_status: string; verification_method: string; rating: number | null; review_count: number; review_note?: string; reviews?: Review[]; check?: { result: string; detail: string; source_url: string }; matching_modes?: VisitMode[] }
export type Slot = { id: number; doctor_id: number; mode: VisitMode; start_at: string; end_at: string; status: string }
export type PrescriptionStatus = 'draft' | 'queued' | 'processing' | 'ready' | 'unreadable' | 'unavailable'
export type Prescription = {
  status: PrescriptionStatus; version: string; photo_count: number
  entries: { kind: 'date' | 'medication' | 'note' | 'unreadable'; text: string; page: number }[]
}
export type Refund = { amount_paise: number; status: 'queued' | 'processing' | 'processed' | 'needs_review'; reference: string | null }
export type CancellationQuote = { paid_paise: number; fee_percent: number; fee_paise: number; refund_paise: number }
export type Appointment = {
  prescription_status?: PrescriptionStatus | null; prescription_consent?: string | null
  id: number; slot_id: number; doctor_id: number; patient_id: number | null; doctor_name: string; patient_name: string
  mode: VisitMode; booking_kind: 'scheduled' | 'urgent'; status: string; amount_paise: number; amount_rupees: number
  start_at: string; end_at: string; clinic_name: string; clinic_address: string; hold_expires_at: string; reviewed?: boolean
  can_cancel: boolean; payment_processing: boolean; cancelled_at: string | null; cancelled_by: 'patient' | 'guest' | 'doctor' | 'admin' | null
  cancellation_fee_paise: number | null; refund: Refund | null
}
export type Review = { id: number; rating: number; comment: string; created_at: string }
export type Notice = {
  id: number; body: string; created_at: string; read_at: string | null
  target: { type: 'appointment'; appointment_id: number } | { type: 'doctor_profile' } | null
}
export type NotificationInbox = { items: Notice[]; unread_count: number; next_before_id: number | null; through_id: number | null }
export type Checkout = { key: string; order_id: string; amount: number; currency: string }

export class ApiError extends Error {
  constructor(message: string, public status: number, public detail?: unknown) {
    super(message)
  }
}

function simulateLatency(signal?: AbortSignal | null) {
  signal?.throwIfAborted()
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', abort)
      resolve()
    }, 1200)
    function abort() {
      window.clearTimeout(timer)
      reject(signal?.reason)
    }
    signal?.addEventListener('abort', abort, { once: true })
  })
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    if (import.meta.env.DEV && import.meta.env.MODE === 'slow') await simulateLatency(options.signal)
    response = await fetch(`/api/v1${path}`, { credentials: 'same-origin', ...options, headers })
  } catch (error) {
    if (options.signal?.aborted) throw error
    throw new Error('We couldn’t connect to MediConnect. Try again.')
  }
  if (!response.ok) {
    let message = 'Something went wrong. Please try again.'
    let detail: unknown
    try {
      const body = await response.json()
      detail = body.detail
      if (typeof body.detail === 'string') {
        message = body.detail
      } else if (Array.isArray(body.detail) && body.detail[0]?.msg) {
        const issue = body.detail[0]
        const key = String(issue.loc?.at(-1) ?? '').replaceAll('_', ' ')
        const reason = String(issue.msg).replace(/^Value error, /, '')
        message = issue.type === 'value_error' || !key ? reason : `Check ${key}. ${reason}.`
      } else if (typeof body.detail?.message === 'string') {
        message = body.detail.message
      }
    } catch { /* use fallback */ }
    throw new ApiError(message, response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
}

export function dateTime(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Kolkata' }).format(new Date(value))
}

export function rupees(paise: number): string {
  const hasPaise = paise % 100 !== 0
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: hasPaise ? 2 : 0 }).format(paise / 100)
}

export function dateOnly(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' }).format(new Date(value))
}

export function indiaDate(value: string): string {
  const parts = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value))
  const date = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${date.year}-${date.month}-${date.day}`
}

export function nameWithoutTitle(name: string): string {
  return name.trim().replace(/^(?:dr(?:\.\s*|\s+))+/i, '').trim()
}

export function doctorDisplayName(name: string): string {
  return `Dr. ${nameWithoutTitle(name)}`
}

export function initials(name: string): string {
  return nameWithoutTitle(name).split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0].toUpperCase()).join('')
}
