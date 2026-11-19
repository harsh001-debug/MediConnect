import { useState } from 'react'
import type { ComponentType } from 'react'
import { Activity, CalendarDays, ChartNoAxesCombined, Check, ChevronDown, Clock3, CreditCard, LayoutDashboard, RefreshCw, ShieldCheck, Stethoscope, Users } from 'lucide-react'
import { Navigate, NavLink, useParams } from 'react-router-dom'
import { ErrorBox, useAuth } from './Account'
import { dateTime, initials, rupees } from './api'
import { adminPath, AdminTable, RangeControl, useAdminData } from './AdminData'
import type { ReportRange } from './AdminData'
import { AccountsPanel, AppointmentsPanel, DoctorReviewsPanel, SlotsPanel, TransactionsPanel } from './AdminSections'
import { ActionButton, LoadingPreview, Skeleton } from './ui'

const sections = [
  { id: 'overview', title: 'Overview', icon: LayoutDashboard },
  { id: 'appointments', title: 'Appointments', icon: CalendarDays },
  { id: 'slots', title: 'Slots', icon: Clock3 },
  { id: 'accounts', title: 'Accounts', icon: Users },
  { id: 'doctors', title: 'Doctor reviews', icon: ShieldCheck },
  { id: 'transactions', title: 'Transactions', icon: CreditCard },
] as const

export type PanelProps = { range: ReportRange; revision: number; onChanged: (message: string) => void }

export function AdminDashboard() {
  const { user } = useAuth()
  const { section = 'overview' } = useParams()
  const current = sections.find(item => item.id === section)
  const [range, setRange] = useState<ReportRange>({ period: 'week' })
  const [revision, setRevision] = useState(0)
  const [notice, setNotice] = useState('')
  if (!current) return <Navigate to="/admin" replace />
  const usesRange = ['overview', 'appointments', 'slots', 'transactions'].includes(section)
  const reportKey = [range.period, range.start, range.end].join('/')
  const onChanged = (message: string) => {
    setNotice(message)
    setRevision(value => value + 1)
  }
  const props = { range, revision, onChanged }
  const menu = <nav aria-label="Admin sections">{sections.map(({ id, title, icon: Icon }) =>
    <NavLink key={id} end to={id === 'overview' ? '/admin' : '/admin/' + id}>
      <Icon size={18} aria-hidden="true" />{title}
    </NavLink>)}
  </nav>

  return <div className="admin-stage">
    <div className="admin-layout shell">
      <aside className="admin-sidebar">
        <div className="admin-sidebar-title"><ShieldCheck size={21} aria-hidden="true" /> <span>Admin panel</span></div>
        {menu}
        <div className="admin-user"><span className="admin-user-avatar">{initials(user?.full_name ?? 'Admin')}</span>
          <div><strong>{user?.full_name}</strong><small>Administrator</small></div></div>
      </aside>
      <details className="admin-mobile-nav" key={section}>
        <summary><ShieldCheck size={18} aria-hidden="true" /> Admin sections <ChevronDown size={17} aria-hidden="true" /></summary>
        {menu}
      </details>
      <div className="admin-content">
        <div className="admin-page-heading"><h1>{current.title}</h1></div>
        {usesRange && <RangeControl range={range} onChange={setRange} />}
        {notice && <div className="admin-notice success-box" role="status">
          <Check size={17} aria-hidden="true" /><span>{notice}</span>
          <button className="text-button" onClick={() => setNotice('')}>Dismiss</button>
        </div>}
        {section === 'overview' && <Overview range={range} revision={revision} />}
        {section === 'appointments' && <AppointmentsPanel key={reportKey} {...props} />}
        {section === 'slots' && <SlotsPanel key={reportKey} {...props} />}
        {section === 'accounts' && <AccountsPanel {...props} />}
        {section === 'doctors' && <DoctorReviewsPanel {...props} />}
        {section === 'transactions' && <TransactionsPanel key={reportKey} {...props} />}
      </div>
    </div>
  </div>
}

type Counters = {
  bookings: number; cancellations: number; slots: number; booked_slots: number; payments: number
  gross_paise: number; refunded_paise: number; pending_refund_paise: number; review_refund_paise: number; net_paise: number
}
type ReportDay = Counters & { date: string }
type OverviewReport = {
  start: string; end: string; as_of: string; accounts: number; patients: number; doctors: number; published_doctors: number
  totals: Counters; days: ReportDay[]
}
const number = (value: number) => value.toLocaleString('en-IN')
const dayFormat = new Intl.DateTimeFormat('en-IN', { timeZone: 'UTC', day: 'numeric', month: 'short' })
const dayLabel = (date: string) => dayFormat.format(new Date(date + 'T00:00:00Z'))

function rangeLabel(start: string, end: string) {
  const startYear = start.slice(0, 4)
  const endYear = end.slice(0, 4)
  if (start === end) return dayLabel(end) + ' ' + endYear
  return dayLabel(start) + (startYear === endYear ? '' : ' ' + startYear) + ' – ' + dayLabel(end) + ' ' + endYear
}

function Metric({ title, value, detail, icon: Icon }: {
  title: string; value: string; detail: string; icon: ComponentType<{ size: number; 'aria-hidden': true }>
}) {
  return <article className="admin-metric">
    <div><span>{title}</span><span className="admin-metric-icon"><Icon size={19} aria-hidden={true} /></span></div>
    <strong>{value}</strong><p>{detail}</p>
  </article>
}

type Series = { key: keyof Counters; label: string; color: string }

function DailyChart({ title, days, series, format }: {
  title: string; days: ReportDay[]; series: Series[]; format: (value: number) => string
}) {
  const highest = Math.max(0, ...days.flatMap(day => series.map(item => day[item.key])))
  const scale = highest <= 4 ? Math.max(1, highest) : Math.ceil(highest / 4) * 4
  const ticks = scale <= 4 ? [scale, 0] : [scale, scale / 2, 0]
  const labels = days.filter((_, index) => index === 0 || index === Math.floor(days.length / 2) || index === days.length - 1)
  return <article className="admin-chart-card">
    <h2>{title}</h2>
    {highest === 0 ? <p className="admin-chart-empty">No activity for this period.</p> : <>
    <div className="admin-legend" aria-hidden="true">{series.map(item =>
      <span key={item.key}><i className={item.color} />{item.label}</span>)}</div>
    <div className="admin-chart" aria-hidden="true">
      <div className="admin-chart-axis">{ticks.map(tick => <span key={tick}>{format(tick)}</span>)}</div>
      <div className="admin-chart-content">
        <div className="admin-chart-plot">
          {days.map(day => <div className="admin-chart-day" key={day.date} title={dayLabel(day.date) + ' · ' +
            series.map(item => item.label + ' ' + format(day[item.key])).join(' · ')}>
            {series.map(item => <span key={item.key} className={item.color}
              style={{ height: (day[item.key] / scale * 100) + '%' }} />)}
          </div>)}
        </div>
        <div className="admin-chart-labels">{labels.map(day => <span key={day.date}>{dayLabel(day.date)}</span>)}</div>
      </div>
    </div>
    </>}
    <details className="admin-chart-details"><summary>View daily figures</summary>
      <AdminTable label={title} headings={['Date', ...series.map(item => item.label)]} compact>{days.map(day =>
        <tr key={day.date}><th scope="row">{dayLabel(day.date)}</th>
          {series.map(item => <td key={item.key}>{format(day[item.key])}</td>)}</tr>)}</AdminTable>
    </details>
  </article>
}

function Overview({ range, revision }: { range: ReportRange; revision: number }) {
  const { data, error, busy, reload } = useAdminData<OverviewReport>(adminPath('overview', range), revision, true)
  return <section aria-label="Operations overview">
    <div className="admin-report-meta">
      <div>{data && <>
        <strong>{rangeLabel(data.start, data.end)} IST</strong>
        <span>Updated {dateTime(data.as_of)} IST</span>
      </>}</div>
      <ActionButton className="outline-button small" onClick={reload} pending={busy && !!data} pendingLabel="Refreshing…" disabled={busy}>
        <RefreshCw size={15} aria-hidden="true" /> Refresh
      </ActionButton>
    </div>
    {error && <ErrorBox message={data ? 'Refresh failed. Showing the last successful report. ' + error : error} />}
    {!data && !error && <LoadingPreview label="Loading overview…">
      <div className="admin-metrics">
        {[0, 1, 2, 3, 4, 5].map(index => <div className="admin-metric skeleton-stack" key={index}>
          <div><Skeleton width="64%" /><Skeleton width="30px" height="30px" /></div>
          <Skeleton width="36%" height="25px" /><Skeleton width="78%" />
        </div>)}
      </div>
    </LoadingPreview>}
    {data && <>
      <div className="admin-metrics content-enter">
        <Metric title="Registered accounts" value={number(data.accounts)} detail={number(data.patients) + ' patients · All time'} icon={Users} />
        <Metric title="Doctors" value={number(data.doctors)} detail={number(data.published_doctors) + ' publicly listed · All time'} icon={Stethoscope} />
        <Metric title="Confirmed bookings" value={number(data.totals.bookings)} detail={number(data.totals.cancellations) + ' cancellations in period'} icon={CalendarDays} />
        <Metric title="Booked slots" value={number(data.totals.booked_slots)} detail={'Of ' + number(data.totals.slots) + ' scheduled slots'} icon={Clock3} />
        <Metric title="Captured payments" value={rupees(data.totals.gross_paise)} detail={number(data.totals.payments) + ' Razorpay test captures'} icon={CreditCard} />
        <Metric title="Net after refunds" value={rupees(data.totals.net_paise)} detail={rupees(data.totals.refunded_paise) + ' in processed refunds'} icon={ChartNoAxesCombined} />
      </div>
      <div className="admin-chart-grid content-enter">
        <DailyChart title="Booking activity" days={data.days} format={number} series={[
          { key: 'bookings', label: 'Confirmed bookings', color: 'admin-bar-teal' },
          { key: 'cancellations', label: 'Cancellations', color: 'admin-bar-gold' },
        ]} />
        <DailyChart title="Payment activity" days={data.days} format={rupees} series={[
          { key: 'gross_paise', label: 'Captured', color: 'admin-bar-teal' },
          { key: 'refunded_paise', label: 'Processed refunds', color: 'admin-bar-blue' },
        ]} />
      </div>
      <div className="admin-refund-summary">
        <Activity size={20} aria-hidden="true" />
        <span><strong>{rupees(data.totals.pending_refund_paise)}</strong> pending refunds</span>
        <span><strong>{rupees(data.totals.review_refund_paise)}</strong> in refunds requiring review</span>
        <NavLink to="/admin/transactions">View transactions</NavLink>
      </div>
      <p className="admin-report-note">Bookings and cancellations are counted on the day each occurred. Slots are counted by visit date. Refunds are shown with their original captures.</p>
    </>}
  </section>
}
