import { useEffect, useMemo, useState } from 'react'
import { api, formatINR, formatMonth } from '../api'
import { Card, PageHeader, Loading, EmptyState } from '../components/Common'
import { DonutChart } from '../components/Charts'

const RANGES = [
  { key: 'this_month', label: 'This Month' },
  { key: '3m', label: 'Last 3M' },
  { key: '6m', label: 'Last 6M' },
  { key: 'year', label: 'This Year' },
  { key: 'all', label: 'All' },
]

export default function Categories() {
  const [apiCats, setApiCats] = useState([])
  const [monthly, setMonthly] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [range, setRange] = useState('all')
  const [sortKey, setSortKey] = useState('total')
  const [sortDir, setSortDir] = useState('desc')

  useEffect(() => {
    Promise.all([api.categories(), api.monthly(), api.summary()])
      .then(([c, m, s]) => {
        setApiCats(c)
        setMonthly(m)
        setSummary(s)
      })
      .finally(() => setLoading(false))
  }, [])

  // First month included for the selected range (YYYY-MM); null = all.
  const startMonth = useMemo(() => {
    if (range === 'all') return null
    const pad = (n) => String(n).padStart(2, '0')
    const now = new Date()
    const key = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
    const back = (n) => key(new Date(now.getFullYear(), now.getMonth() - n, 1))
    return { this_month: key(now), '3m': back(3), '6m': back(6), year: back(12) }[range]
  }, [range])

  const allMonths = useMemo(
    () => [...new Set(monthly.map((r) => r.month))].sort(),
    [monthly]
  )

  // Category rows for the active range, plus the human-readable range label.
  const { cats, rangeLabel } = useMemo(() => {
    if (range === 'all') {
      const lbl = allMonths.length
        ? `${formatMonth(allMonths[0])} – ${formatMonth(allMonths[allMonths.length - 1])}`
        : ''
      return { cats: apiCats, rangeLabel: lbl }
    }

    const income = summary?.monthly_income || 0
    const rangeMonths = allMonths.filter((m) => m >= startMonth)
    // Per-category per-month totals across ALL data (for MoM).
    const catMonth = {}
    const totals = {}
    for (const r of monthly) {
      catMonth[r.category] = catMonth[r.category] || {}
      catMonth[r.category][r.month] = (catMonth[r.category][r.month] || 0) + r.total
      if (r.month >= startMonth) totals[r.category] = (totals[r.category] || 0) + r.total
    }
    const last = rangeMonths[rangeMonths.length - 1]
    const prev = last ? allMonths[allMonths.indexOf(last) - 1] : undefined
    const grand = Object.values(totals).reduce((a, b) => a + b, 0) || 1
    const numMonths = rangeMonths.length || 1
    const incomeBase = income * numMonths

    const rows = Object.entries(totals)
      .map(([category, total]) => {
        const cur = (catMonth[category] && catMonth[category][last]) || 0
        const pv = prev ? (catMonth[category] && catMonth[category][prev]) || 0 : 0
        let mom = null
        if (prev) mom = pv > 0 ? ((cur - pv) / pv) * 100 : cur > 0 ? 100 : 0
        return {
          category,
          total: Math.round(total * 100) / 100,
          pct_of_total: Math.round((total / grand) * 1000) / 10,
          pct_of_income: incomeBase ? Math.round((total / incomeBase) * 1000) / 10 : 0,
          mom_change: mom == null ? null : Math.round(mom * 10) / 10,
        }
      })
      .sort((a, b) => b.total - a.total)

    const lbl = rangeMonths.length
      ? `${formatMonth(rangeMonths[0])} – ${formatMonth(last)}`
      : 'No data in this range'
    return { cats: rows, rangeLabel: lbl }
  }, [range, apiCats, monthly, summary, startMonth, allMonths])

  const sorted = useMemo(() => {
    const arr = [...cats]
    arr.sort((a, b) => {
      const av = a[sortKey] ?? -Infinity
      const bv = b[sortKey] ?? -Infinity
      if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av)
      return sortDir === 'asc' ? av - bv : bv - av
    })
    return arr
  }, [cats, sortKey, sortDir])

  if (loading) return <Loading />
  if (!apiCats.length) return (<><PageHeader title="Categories" /><EmptyState /></>)

  const donut = sorted.slice(0, 8).map((c) => ({ name: c.category, value: c.total }))

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const rowBg = (pctIncome) => {
    if (pctIncome > 15) return 'bg-red/10'
    if (pctIncome >= 10) return 'bg-accent/10'
    return 'bg-green/5'
  }

  const Th = ({ label, k, right }) => (
    <th
      onClick={() => toggleSort(k)}
      className={`cursor-pointer select-none px-3 py-2 text-xs uppercase tracking-wider text-muted hover:text-text ${right ? 'text-right' : 'text-left'}`}
    >
      {label} {sortKey === k ? (sortDir === 'asc' ? '▲' : '▼') : ''}
    </th>
  )

  return (
    <>
      <PageHeader title="Categories" subtitle={rangeLabel} />

      {/* Range pills */}
      <div className="mb-4 flex flex-wrap gap-2">
        {RANGES.map((r) => (
          <button
            key={r.key}
            onClick={() => setRange(r.key)}
            className={`rounded-full px-3 py-1 font-sora text-xs transition-colors ${
              range === r.key
                ? 'border border-accent/50 bg-accent/15 text-accent'
                : 'border border-border text-muted hover:text-text'
            }`}
          >
            {r.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <Card title="Spend Split" className="lg:col-span-2">
          {donut.length ? (
            <DonutChart data={donut} height={320} />
          ) : (
            <p className="font-data text-sm text-muted">No spend in this range.</p>
          )}
        </Card>

        <Card title="Breakdown" className="lg:col-span-3">
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-border">
                  <Th label="Category" k="category" />
                  <Th label="Total ₹" k="total" right />
                  <Th label="% Total" k="pct_of_total" right />
                  <Th label="% Income" k="pct_of_income" right />
                  <Th label="MoM" k="mom_change" right />
                </tr>
              </thead>
              <tbody>
                {sorted.map((c) => (
                  <tr key={c.category} className={`border-b border-border/50 ${rowBg(c.pct_of_income)}`}>
                    <td className="px-3 py-2 text-left font-sora text-sm text-text">{c.category}</td>
                    <td className="px-3 py-2 text-right text-sm text-text">₹{formatINR(c.total)}</td>
                    <td className="px-3 py-2 text-right text-sm text-muted">{c.pct_of_total}%</td>
                    <td className="px-3 py-2 text-right text-sm text-muted">{c.pct_of_income}%</td>
                    <td className="px-3 py-2 text-right text-sm">
                      {c.mom_change == null ? (
                        <span className="text-muted">—</span>
                      ) : (
                        <span className={c.mom_change > 0 ? 'text-red' : c.mom_change < 0 ? 'text-green' : 'text-muted'}>
                          {c.mom_change > 0 ? '+' : ''}
                          {c.mom_change}%
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-3 py-6 text-center font-data text-sm text-muted">
                      No spend in this range.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </>
  )
}
