import { useEffect, useMemo, useState } from 'react'
import { api, formatINR, colorFor } from '../api'
import { Card, PageHeader, Loading, EmptyState } from '../components/Common'
import { StackedAreaChart } from '../components/Charts'

export default function Monthly() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [startMonth, setStartMonth] = useState('')
  const [endMonth, setEndMonth] = useState('')
  const [selectedMonth, setSelectedMonth] = useState(null)
  const [monthTxns, setMonthTxns] = useState([])

  useEffect(() => {
    api.monthly().then(setRows).finally(() => setLoading(false))
  }, [])

  const { months, categories } = useMemo(() => {
    const mset = new Set()
    const cset = new Set()
    rows.forEach((r) => {
      mset.add(r.month)
      cset.add(r.category)
    })
    return { months: [...mset].sort(), categories: [...cset].sort() }
  }, [rows])

  // Initialise the date range once data loads.
  useEffect(() => {
    if (months.length && !startMonth) {
      setStartMonth(months[0])
      setEndMonth(months[months.length - 1])
    }
  }, [months]) // eslint-disable-line react-hooks/exhaustive-deps

  const chartData = useMemo(() => {
    const inRange = (m) => (!startMonth || m >= startMonth) && (!endMonth || m <= endMonth)
    const byMonth = {}
    rows.forEach((r) => {
      if (!inRange(r.month)) return
      byMonth[r.month] = byMonth[r.month] || { month: r.month, __total: 0 }
      byMonth[r.month][r.category] = (byMonth[r.month][r.category] || 0) + r.total
      byMonth[r.month].__total += r.total
    })
    return Object.values(byMonth).sort((a, b) => a.month.localeCompare(b.month))
  }, [rows, startMonth, endMonth])

  const loadMonth = (month) => {
    setSelectedMonth(month)
    api.transactions({ month, per_page: 500, sort: 'amount', dir: 'DESC' }).then((d) =>
      setMonthTxns(d.transactions)
    )
  }

  if (loading) return <Loading />
  if (!rows.length) return (<><PageHeader title="Monthly Trends" /><EmptyState /></>)

  return (
    <>
      <PageHeader title="Monthly Trends" subtitle="Click a month on the chart to expand its transactions">
        <label className="font-data text-xs text-muted">From</label>
        <select
          value={startMonth}
          onChange={(e) => setStartMonth(e.target.value)}
          className="rounded-md border border-border bg-bg px-2 py-1 font-data text-xs text-text"
        >
          {months.map((m) => (<option key={m} value={m}>{m}</option>))}
        </select>
        <label className="font-data text-xs text-muted">To</label>
        <select
          value={endMonth}
          onChange={(e) => setEndMonth(e.target.value)}
          className="rounded-md border border-border bg-bg px-2 py-1 font-data text-xs text-text"
        >
          {months.map((m) => (<option key={m} value={m}>{m}</option>))}
        </select>
      </PageHeader>

      <Card title="Spend by Category">
        <StackedAreaChart data={chartData} categories={categories} onMonthClick={loadMonth} />
      </Card>

      {selectedMonth && (
        <Card title={`Transactions — ${selectedMonth}`} className="mt-6">
          {monthTxns.length === 0 ? (
            <p className="font-data text-sm text-muted">No transactions for this month.</p>
          ) : (
            <div className="max-h-96 overflow-y-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="sticky top-0 border-b border-border bg-card">
                    <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">Date</th>
                    <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">Description</th>
                    <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">Category</th>
                    <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {monthTxns.map((t) => (
                    <tr key={t.id} className="border-b border-border/50">
                      <td className="px-3 py-2 text-left text-sm text-muted">{t.date}</td>
                      <td className="px-3 py-2 text-left font-sora text-sm text-text">{t.description}</td>
                      <td className="px-3 py-2 text-left text-sm">
                        <span style={{ color: colorFor(t.category) }}>{t.category}</span>
                      </td>
                      <td className="px-3 py-2 text-right text-sm text-text">₹{formatINR(t.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </>
  )
}
