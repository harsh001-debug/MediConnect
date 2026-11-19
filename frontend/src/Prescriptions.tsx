import { useEffect, useState } from 'react'
import type { ChangeEvent } from 'react'
import { FileImage, X } from 'lucide-react'
import { api, ApiError } from './api'
import type { Appointment, Prescription } from './api'
import { ActionButton, Loading } from './ui'
import { ErrorBox } from './Account'

const MAX_PHOTOS = 3
const MAX_BYTES = 8 * 1024 * 1024
const MIME_TYPES = ['image/jpeg', 'image/png', 'image/webp']

export type PrescriptionSelection = { photos: File[]; consent: boolean }
export const emptyPrescription: PrescriptionSelection = { photos: [], consent: false }

export function savePrescription(appointmentId: number, selection: PrescriptionSelection, version: string) {
  const body = new FormData()
  body.set('consent_version', selection.consent ? version : '')
  selection.photos.forEach(file => body.append('photos', file))
  return api<Prescription>(`/appointments/${appointmentId}/prescription`, { method: 'PUT', body })
}

export function removePrescription(appointmentId: number) {
  return api<void>(`/appointments/${appointmentId}/prescription`, { method: 'DELETE' })
}

export function PrescriptionPicker({ selection, onChange, disabled }: {
  selection: PrescriptionSelection; onChange: (selection: PrescriptionSelection) => void; disabled: boolean
}) {
  const [previews, setPreviews] = useState<{ file: File; url: string }[]>([])
  const [error, setError] = useState('')
  const { photos, consent } = selection

  useEffect(() => {
    const urls = photos.map(file => ({ file, url: URL.createObjectURL(file) }))
    setPreviews(urls)
    return () => urls.forEach(({ url }) => URL.revokeObjectURL(url))
  }, [photos])

  const pick = (event: ChangeEvent<HTMLInputElement>) => {
    const added = Array.from(event.currentTarget.files ?? [])
    event.currentTarget.value = ''
    if (!added.length) return
    setError('')
    if (photos.length + added.length > MAX_PHOTOS) { setError('Choose up to three photos.'); return }
    if (added.some(file => file.size > MAX_BYTES)) { setError('Each photo must be 8 MiB or smaller.'); return }
    if (added.some(file => !MIME_TYPES.includes(file.type) && !/\.(jpe?g|png|webp)$/i.test(file.name))) {
      setError('Choose JPEG, PNG, or WebP photos.')
      return
    }
    onChange({ photos: [...photos, ...added], consent: false })
  }

  return <div className="prescription-picker">
    <label className="prescription-input">Prescription photos
      <input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={pick}
        disabled={disabled || photos.length >= MAX_PHOTOS} />
    </label>
    <p className="muted">Up to 3 photos, 8 MiB each. Use clear images with the full page in view.</p>
    {error && <ErrorBox message={error} />}
    {photos.length > 0 && <>
      <ul className="prescription-previews">
        {previews.map(({ file, url }, index) => <li key={url}>
          <img src={url} alt={`Selected page ${index + 1}`} />
          <span title={file.name}>Page {index + 1}</span>
          <button type="button" className="text-button" disabled={disabled} aria-label={`Remove page ${index + 1}`}
            onClick={() => { setError(''); onChange({ photos: photos.filter((_, page) => page !== index), consent: false }) }}>
            <X size={16} />
          </button>
        </li>)}
      </ul>
      <label className="prescription-consent">
        <input type="checkbox" checked={consent} disabled={disabled} onChange={event => onChange({ photos, consent: event.target.checked })} />
        <span>I agree to have Google AI read these photos and to share the photos and AI summary with the doctor assigned to this visit after booking is confirmed.</span>
      </label>
    </>}
  </div>
}

const entryLabel = { date: 'Date', medication: 'Medication', note: 'Note', unreadable: 'Needs review' }

function PrescriptionContents({ record, endpoint }: { record: Prescription; endpoint: string }) {
  const photoUrl = (page: number) => `/api/v1${endpoint}/photos/${record.version}/${page}`
  return <>
    <div className="prescription-pages" aria-label="Source photos">
      {Array.from({ length: record.photo_count }, (_, index) => <a className="outline-button small" key={index}
        href={photoUrl(index + 1)} target="_blank" rel="noreferrer"><FileImage size={16} /> View page {index + 1}</a>)}
    </div>
    {record.status === 'draft' && <p className="muted">Saved privately. Your doctor can see these photos after booking is confirmed.</p>}
    {(record.status === 'queued' || record.status === 'processing') &&
      <Loading inline>Preparing the AI summary. You can open the photos now.</Loading>}
    {record.status === 'unavailable' && <p className="muted" role="status">The AI summary is unavailable. Open the source photos to review the prescription.</p>}
    {record.status === 'unreadable' && <p className="muted" role="status">AI could not read the prescription reliably. Check the source photos.</p>}
    {record.status === 'ready' && <>
      <p className="prescription-review-label"><strong>AI-generated</strong> · Verify against the source photos</p>
      <ul className="prescription-entries">
        {record.entries.map((entry, index) => <li key={index}>
          <span><strong>{entryLabel[entry.kind]}</strong> {entry.text}</span>
          <a className="text-button" href={photoUrl(entry.page)} target="_blank" rel="noreferrer">Page {entry.page}</a>
        </li>)}
      </ul>
    </>}
  </>
}

export function PrescriptionPanel({ item, editable, disabled, onChanged, onSaving }: {
  item: Appointment; editable: boolean; disabled: boolean; onChanged: () => void; onSaving: (saving: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  const [record, setRecord] = useState<Prescription | null>(null)
  const [selection, setSelection] = useState(emptyPrescription)
  const [loading, setLoading] = useState(false)
  const [operation, setOperation] = useState<'save' | 'remove' | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const status = item.prescription_status
  const version = editable ? item.prescription_consent : null
  const endpoint = `/appointments/${item.id}/prescription`
  const saving = operation !== null

  useEffect(() => {
    if (!open || !status) return
    const controller = new AbortController()
    let timer: number | undefined
    let polls = 0
    setLoading(true)
    setError('')
    const load = async () => {
      try {
        const result = await api<Prescription>(endpoint, { signal: controller.signal })
        if (controller.signal.aborted) return
        setRecord(result)
        if ((result.status === 'queued' || result.status === 'processing') && polls < 24) {
          polls += 1
          timer = window.setTimeout(() => void load(), 5000)
        }
      } catch (err) {
        if (controller.signal.aborted) return
        setRecord(null)
        setError(err instanceof ApiError && err.status === 404 ? 'This prescription is no longer available.' : (err as Error).message)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [endpoint, open, refresh, status])

  const update = async (remove: boolean) => {
    setOperation(remove ? 'remove' : 'save')
    onSaving(true)
    setError('')
    try {
      if (remove) {
        await removePrescription(item.id)
        setRecord(null)
      } else if (version) {
        setRecord(await savePrescription(item.id, selection, version))
      }
      setSelection(emptyPrescription)
      onChanged()
    } catch (err) { setError((err as Error).message) }
    finally { setOperation(null); onSaving(false) }
  }

  const current = status || editable ? record : null
  return <details className="prescription-panel" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><FileImage size={17} /> {status || current ? 'Previous prescription' : 'Upload a previous prescription (optional)'}</summary>
    {open && <div className="prescription-content">
      {loading && <Loading inline>Loading prescription…</Loading>}
      {current && <PrescriptionContents record={current} endpoint={endpoint} />}
      {(current?.status === 'queued' || current?.status === 'processing') && <button type="button" className="text-button"
        onClick={() => setRefresh(value => value + 1)}>Refresh summary</button>}
      {error && <><ErrorBox message={error} />{status && !current && <button type="button" className="text-button"
        onClick={() => setRefresh(value => value + 1)}>Refresh prescription</button>}</>}
      {editable && !loading && <>
        {version && <PrescriptionPicker selection={selection} onChange={setSelection} disabled={saving || disabled} />}
        {selection.photos.length > 0 && <p className="muted">Save these photos before completing payment.</p>}
        <div className="prescription-pages">
          {version && selection.photos.length > 0 && <ActionButton type="button" className="outline-button small"
            disabled={saving || disabled || !selection.consent} pending={operation === 'save'} pendingLabel="Saving photos…"
            onClick={() => void update(false)}>{current ? 'Replace saved photos' : 'Save photos'}</ActionButton>}
          {(current || status === 'draft') && <ActionButton type="button" className="text-button danger" disabled={saving || disabled}
            pending={operation === 'remove'} pendingLabel="Removing photos…" onClick={() => void update(true)}>Remove saved photos</ActionButton>}
        </div>
      </>}
    </div>}
  </details>
}
