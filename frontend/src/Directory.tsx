import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, ChevronDown, Clock3, Hospital, MapPin, Search, SlidersHorizontal, Star, Stethoscope, Video } from 'lucide-react'
import { api, dateOnly, doctorDisplayName, rupees } from './api'
import type { Doctor } from './api'
import { Avatar, EmptyState, LoadingPreview, Skeleton } from './ui'
import { ErrorBox, Field } from './Account'

export function Rating({ rating, count }: { rating: number | null; count: number }) {
  if (rating === null) return <span className="muted no-rating">No reviews yet</span>
  return <span className="rating"><Star size={15} fill="currentColor" aria-hidden="true" /> {rating}
    <span className="muted">({count} {count === 1 ? 'review' : 'reviews'})</span></span>
}

function DoctorCard({ doctor, directoryQuery = '' }: { doctor: Doctor; directoryQuery?: string }) {
  const options = new URLSearchParams()
  const date = new URLSearchParams(directoryQuery).get('available_on')
  const mode = doctor.matching_modes?.length === 1 ? doctor.matching_modes[0] : null
  if (mode) options.set('mode', mode)
  if (date) options.set('available_on', date)
  const fee = mode === 'online' ? doctor.online_fee_rupees : mode === 'clinic' ? doctor.clinic_fee_rupees
    : Math.min(doctor.clinic_fee_rupees, doctor.online_fee_rupees)
  const name = doctorDisplayName(doctor.full_name)
  return <article className="doctor-card">
    <div className="doctor-card-top">
      <Avatar name={name} />
      <div><h3>{name}</h3><p className="doctor-specialty">{doctor.specialty}</p></div>
    </div>
    <p className="doctor-meta"><span><MapPin size={15} aria-hidden="true" /> {doctor.city}</span>
      <span><Stethoscope size={15} aria-hidden="true" /> {doctor.experience_years} years of experience</span></p>
    <div className="doctor-card-bottom">
      <Rating rating={doctor.rating} count={doctor.review_count} />
      <span className="doctor-price">{mode ? '' : 'From '}{rupees(fee * 100)}</span>
    </div>
    <Link to={{ pathname: `/doctors/${doctor.id}`, search: options.toString() }}
      state={{ directoryQuery }} className="card-link" aria-label={`View profile for ${name}`}>
      View profile <ArrowRight size={16} aria-hidden="true" />
    </Link>
  </article>
}

function DoctorCardsLoading({ directory = false }: { directory?: boolean }) {
  return <LoadingPreview label={directory ? 'Searching for doctors…' : 'Loading doctors…'}>
    <div className={directory ? 'doctor-grid explore-grid' : 'doctor-grid'}>
      {(directory ? [0, 1] : [0, 1, 2]).map(index => <div className="doctor-card" key={index}>
        <div className="doctor-card-top">
          <Skeleton width="55px" height="55px" />
          <div className="skeleton-stack"><Skeleton width="72%" /><Skeleton width="48%" /></div>
        </div>
        <div className="skeleton-stack"><Skeleton width="47%" /><Skeleton width="69%" /></div>
        <div className="doctor-card-bottom"><Skeleton width="35%" /><Skeleton width="20%" /></div>
        <div className="skeleton-card-action"><Skeleton width="36%" height="20px" /></div>
      </div>)}
    </div>
  </LoadingPreview>
}

function searchParams(form: HTMLFormElement) {
  const params = new URLSearchParams()
  for (const [key, field] of new FormData(form)) {
    const value = String(field).trim()
    if (value) params.set(key, value)
  }
  return params
}

export function Home() {
  const navigate = useNavigate()
  const [doctors, setDoctors] = useState<Doctor[] | null>(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    api<Doctor[]>('/doctors', { signal: controller.signal }).then(rows => setDoctors(rows.slice(0, 3))).catch(err => {
      if (!controller.signal.aborted) setError(err.message)
    })
    return () => controller.abort()
  }, [revision])

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const params = searchParams(event.currentTarget)
    navigate({ pathname: '/doctors', search: params.toString() })
  }

  return <>
    <section className="directory-hero">
      <div className="shell directory-hero-grid">
        <div className="directory-copy">
          <h1>Find a doctor.<br />Book a visit.</h1>
          <p>Compare doctors, fees and available times for a clinic visit or video consultation.</p>
          <form className="directory-search" onSubmit={search} role="search" aria-label="Find a doctor">
            <Field label="Specialty" name="specialty" placeholder="e.g. Dermatology" type="search" />
            <Field label="City" name="city" placeholder="e.g. Kolkata" type="search" />
            <button className="button"><Search size={18} aria-hidden="true" /> Search</button>
          </form>
        </div>
        <section className="visit-options" aria-labelledby="visit-options-title">
          <h2 id="visit-options-title">Choose a visit</h2>
          <Link to="/doctors?mode=clinic"><Hospital size={22} aria-hidden="true" /><span><strong>Clinic visit</strong><small>See doctors by city</small></span><ArrowRight size={18} aria-hidden="true" /></Link>
          <Link to="/doctors?mode=online"><Video size={22} aria-hidden="true" /><span><strong>Video consultation</strong><small>See doctors who consult online</small></span><ArrowRight size={18} aria-hidden="true" /></Link>
          <Link to="/urgent"><Clock3 size={22} aria-hidden="true" /><span><strong>Urgent appointment</strong><small>See times in the next 24 hours</small></span><ArrowRight size={18} aria-hidden="true" /></Link>
        </section>
      </div>
    </section>
    <section className="section shell" aria-labelledby="browse-doctors-title">
      <div className="section-heading row"><h2 id="browse-doctors-title">Browse doctors</h2>
        <Link className="outline-button small" to="/doctors">View directory <ArrowRight size={17} aria-hidden="true" /></Link>
      </div>
      {error ? <><ErrorBox message={error} /><button className="outline-button small" onClick={() => setRevision(value => value + 1)}>Try again</button></>
        : doctors === null ? <DoctorCardsLoading />
        : doctors.length ? <div className="doctor-grid content-enter">{doctors.map(doctor => <DoctorCard doctor={doctor} key={doctor.id} />)}</div>
        : <EmptyState icon={<Stethoscope />} title="No doctors listed yet" text="Check back when doctors have joined the directory." />}
    </section>
  </>
}

const filterNames = ['specialty', 'city', 'mode', 'max_fee', 'available_on'] as const

export function Explore() {
  const [params, setParams] = useSearchParams()
  const [result, setResult] = useState<{ query: string; doctors?: Doctor[]; error?: string } | null>(null)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [revision, setRevision] = useState(0)
  const resultsHead = useRef<HTMLHeadingElement>(null)
  const query = params.toString()
  const current = result?.query === query ? result : null
  const activeFilters = filterNames.filter(name => params.get(name))

  useEffect(() => {
    const controller = new AbortController()
    api<Doctor[]>(`/doctors?${query}`, { signal: controller.signal })
      .then(doctors => { if (!controller.signal.aborted) setResult({ query, doctors }) })
      .catch(err => { if (!controller.signal.aborted) setResult({ query, error: err.message }) })
    return () => controller.abort()
  }, [query, revision])

  function focusResults() {
    requestAnimationFrame(() => resultsHead.current?.focus())
  }

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setParams(searchParams(event.currentTarget))
    setFiltersOpen(false)
    focusResults()
  }

  function clearFilters() {
    setParams({})
    if (activeFilters.length) focusResults()
  }

  function removeFilter(name: typeof filterNames[number]) {
    const next = new URLSearchParams(params)
    next.delete(name)
    setParams(next)
    focusResults()
  }

  function filterLabel(name: typeof filterNames[number]) {
    const value = params.get(name)!
    if (name === 'mode') return value === 'clinic' ? 'Clinic visits' : value === 'online' ? 'Video consultations' : value
    if (name === 'max_fee') return `Up to ₹${value}`
    if (name === 'available_on' && !Number.isNaN(Date.parse(value))) return dateOnly(value)
    return value
  }

  const count = current?.doctors?.length ?? 0
  return <div className="page-shell shell">
    <div className="page-intro"><h1>Find a doctor</h1><p>Filter by specialty, location, fee or appointment date.</p></div>
    <button type="button" className="outline-button small mobile-filter-toggle" aria-expanded={filtersOpen}
      aria-controls="doctor-filters" onClick={() => setFiltersOpen(value => !value)}>
      <SlidersHorizontal size={17} aria-hidden="true" /> Filters{activeFilters.length > 0 && ` (${activeFilters.length})`}
      <ChevronDown size={17} aria-hidden="true" />
    </button>
    <div className="explore-layout">
      <form id="doctor-filters" aria-label="Filter doctors" className={`filter-panel${filtersOpen ? ' is-open' : ''}`} key={query} onSubmit={search}>
        <div className="filter-title"><SlidersHorizontal size={18} aria-hidden="true" /><b>Filters</b>
          <button type="reset" className="text-button" onClick={clearFilters}>Clear all</button></div>
        <Field label="Specialty" name="specialty" placeholder="e.g. Dermatology" defaultValue={params.get('specialty') ?? ''} />
        <Field label="City" name="city" placeholder="e.g. Kolkata" defaultValue={params.get('city') ?? ''} />
        <label>Visit type<select name="mode" defaultValue={params.get('mode') ?? ''}>
          <option value="">Any visit type</option><option value="clinic">Clinic visit</option><option value="online">Video consultation</option>
        </select></label>
        <Field label="Maximum fee (₹)" name="max_fee" type="number" min="1" placeholder="Any fee" defaultValue={params.get('max_fee') ?? ''} />
        <Field label="Appointment date" name="available_on" type="date" defaultValue={params.get('available_on') ?? ''} />
        <button className="button button-small filter-apply">Apply filters</button>
      </form>
      <section className="results-area" aria-labelledby="doctor-results">
        <h2 id="doctor-results" className="results-head" tabIndex={-1}>
          {current?.error ? 'Search unavailable' : current ? `${count} ${count === 1 ? 'doctor' : 'doctors'} found` : 'Doctor results'}
        </h2>
        <span className="visually-hidden" role="status">{current?.doctors ? `${count} ${count === 1 ? 'doctor' : 'doctors'} found` : ''}</span>
        {!!activeFilters.length && <ul className="filter-chips" aria-label="Applied filters">
          {activeFilters.map(name => <li key={name}><button type="button" onClick={() => removeFilter(name)} aria-label={`Remove filter ${filterLabel(name)}`}>
            {filterLabel(name)} <span aria-hidden="true">×</span>
          </button></li>)}
        </ul>}
        {current?.error ? <><ErrorBox message={current.error} /><button className="outline-button small" onClick={() => {
          setResult(null)
          setRevision(value => value + 1)
        }}>Try again</button></>
          : !current ? <DoctorCardsLoading directory />
          : current.doctors?.length ? <div className="doctor-grid explore-grid content-enter">
            {current.doctors.map(doctor => <DoctorCard doctor={doctor} directoryQuery={query} key={doctor.id} />)}
          </div>
          : <EmptyState icon={<Search />} title="No doctors found" text="Try a different specialty, city or date."
            action={activeFilters.length ? <button className="outline-button small" onClick={clearFilters}>Clear filters</button> : undefined} />}
      </section>
    </div>
  </div>
}
