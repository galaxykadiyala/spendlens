import { useEffect, useMemo, useState } from 'react'
import { api, formatINR } from '../api'
import StatCard from '../components/StatCard'
import { Card, PageHeader, Loading, EmptyState, InsightCard } from '../components/Common'
import { DonutChart, IncomeSpendBar } from '../components/Charts'

export default function Overview() {
  const [summary, setSummary] = useState(null)
  const [monthly, setMonthly] = useState([]) // [{month, category, total}]
  const [insights, setInsights] = useState([])
  const [loading, setLoading] = useState(true)
  const [fromMonth, setFromMonth] = useState('')
  const [toMonth, setToMonth] = useState('')

  useEffect(() => {
    Promise.all([api.summary(), api.monthly(), api.insights()])
      .then(([s, m, i]) => {
        setSummary(s)
        setMonthly(m)
        setInsights(i)
      })
      .finally(() => setLoading(false))
  }, [])

  const months = useMemo(() => {
    const set = new Set(monthly.map((r) => r.month))
    return [...set].sort()
  }, [monthly])

  // Default the range to the full span once data loads.
  useEffect(() => {
    if (months.length && !fromMonth) {
      setFromMonth(months[0])
      setToMonth(months[months.length - 1])
    }
  }, [months]) // eslint-disable-line react-hooks/exhaustive-deps

  const inRange = (m) =>
    (!fromMonth || m >= fromMonth) && (!toMonth || m <= toMonth)

  // Derived stats for the selected range.
  const view = useMemo(() => {
    const income = summary?.monthly_income || 0
    const monthTotals = {}
    const catTotals = {}
    for (const r of monthly) {
      if (!inRange(r.month)) continue
      monthTotals[r.month] = (monthTotals[r.month] || 0) + r.total
      catTotals[r.category] = (catTotals[r.category] || 0) + r.total
    }
    const monthList = Object.keys(monthTotals).sort()
    const total = Object.values(monthTotals).reduce((a, b) => a + b, 0)
    const n = monthList.length || 1
    const monthlyAvg = total / n
    const totalIncome = income * n
    const savingsRate = totalIncome ? ((totalIncome - total) / totalIncome) * 100 : 0
    const top6 = Object.entries(catTotals)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([name, value]) => ({ name, value }))
    // Per-month rows with MoM delta.
    const rows = monthList.map((m, i) => {
      const prev = i > 0 ? monthTotals[monthList[i - 1]] : null
      const delta = prev ? ((monthTotals[m] - prev) / prev) * 100 : null
      return { month: m, total: monthTotals[m], delta }
    })
    return { income, total, n, monthlyAvg, savingsRate, top6, rows }
  }, [monthly, fromMonth, toMonth, summary])

  if (loading) return <Loading />
  if (!summary || summary.transaction_count === 0) {
    return (
      <>
        <PageHeader title="Overview" />
        <EmptyState />
      </>
    )
  }

  const fullRange = fromMonth === months[0] && toMonth === months[months.length - 1]

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle={`${fromMonth} → ${toMonth} · ${view.n} month${view.n === 1 ? '' : 's'} · ${summary.card_count} cards`}
      >
        <label className="font-data text-xs text-muted">From</label>
        <select
          value={fromMonth}
          onChange={(e) => setFromMonth(e.target.value)}
          className="rounded-md border border-border bg-bg px-2 py-1 font-data text-xs text-text"
        >
          {months.map((m) => (<option key={m} value={m}>{m}</option>))}
        </select>
        <label className="font-data text-xs text-muted">To</label>
        <select
          value={toMonth}
          onChange={(e) => setToMonth(e.target.value)}
          className="rounded-md border border-border bg-bg px-2 py-1 font-data text-xs text-text"
        >
          {months.map((m) => (<option key={m} value={m}>{m}</option>))}
        </select>
      </PageHeader>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Total Spend" prefix="₹" value={formatINR(view.total)} sub={`${view.n} month${view.n === 1 ? '' : 's'} selected`} accent="accent" />
        <StatCard label="Monthly Avg" prefix="₹" value={formatINR(view.monthlyAvg)} sub={`over ${view.n} month${view.n === 1 ? '' : 's'}`} />
        <StatCard
          label="Savings Rate"
          value={`${view.savingsRate.toFixed(1)}%`}
          sub={`income ₹${formatINR(view.income)}/mo`}
          accent={view.savingsRate >= 40 ? 'green' : view.savingsRate < 0 ? 'red' : 'text'}
        />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Income vs Spend (monthly avg)">
          <IncomeSpendBar income={view.income} spend={view.monthlyAvg} />
          <div className="mt-3 flex justify-between font-data text-xs">
            <span className="text-accent">Spent ₹{formatINR(view.monthlyAvg)}</span>
            <span className="text-green">
              Saved ₹{formatINR(Math.max(view.income - view.monthlyAvg, 0))}
            </span>
          </div>
        </Card>

        <Card title="Top 6 Categories (selected range)">
          {view.top6.length ? <DonutChart data={view.top6} /> : (
            <p className="font-data text-sm text-muted">No spend in this range.</p>
          )}
        </Card>
      </div>

      <Card title="Spend by Month" className="mt-6">
        <div className="max-h-80 overflow-y-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="sticky top-0 border-b border-border bg-card">
                <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">Month</th>
                <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">Spend</th>
                <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">vs prev</th>
              </tr>
            </thead>
            <tbody>
              {view.rows.map((r) => (
                <tr key={r.month} className="border-b border-border/50">
                  <td className="px-3 py-2 text-left font-data text-sm text-text">{r.month}</td>
                  <td className="px-3 py-2 text-right font-data text-sm text-text">₹{formatINR(r.total)}</td>
                  <td className="px-3 py-2 text-right font-data text-sm">
                    {r.delta == null ? (
                      <span className="text-muted">—</span>
                    ) : (
                      <span className={r.delta > 0 ? 'text-red' : r.delta < 0 ? 'text-green' : 'text-muted'}>
                        {r.delta > 0 ? '+' : ''}{r.delta.toFixed(0)}%
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <h2 className="mb-3 mt-8 font-sora text-sm font-600 uppercase tracking-wider text-muted">
        Top Insights {fullRange ? '' : '(across all statements)'}
      </h2>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {insights.slice(0, 3).map((ins, i) => (
          <InsightCard key={i} insight={ins} />
        ))}
        {insights.length === 0 && (
          <p className="font-data text-sm text-muted">No insights yet.</p>
        )}
      </div>
    </>
  )
}
