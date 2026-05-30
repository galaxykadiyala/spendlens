import { useEffect, useMemo, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { api, formatINR, colorFor } from '../api'
import { Card, PageHeader, Loading, EmptyState } from '../components/Common'

export default function Rewards() {
  const [summary, setSummary] = useState([])
  const [rates, setRates] = useState([])
  const [optimize, setOptimize] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([api.rewards.summary(), api.rewards.rates(), api.rewards.optimize()])
      .then(([s, r, o]) => {
        setSummary(s)
        setRates(r)
        setOptimize(o)
      })
      .finally(() => setLoading(false))
  }, [])

  // Latest month's earned per card (for the overview cards).
  const latestEarned = useMemo(() => {
    const m = {}
    for (const row of summary) {
      if (!m[row.card] || row.statement_month > m[row.card].month) {
        m[row.card] = { month: row.statement_month, earned: row.earned }
      }
    }
    return m
  }, [summary])

  // Trend data: [{ month, <card>: closing_balance, ... }]
  const { trend, cards } = useMemo(() => {
    const byMonth = {}
    const cardSet = new Set()
    for (const row of summary) {
      cardSet.add(row.card)
      byMonth[row.statement_month] = byMonth[row.statement_month] || { month: row.statement_month }
      byMonth[row.statement_month][row.card] = row.closing_balance
    }
    const trend = Object.values(byMonth).sort((a, b) => a.month.localeCompare(b.month))
    return { trend, cards: [...cardSet].sort() }
  }, [summary])

  if (loading) return <Loading />

  if (!summary.length) {
    return (
      <>
        <PageHeader title="Rewards" />
        <EmptyState message="No rewards data yet. Rewards are extracted automatically when you re-parse your statements." />
      </>
    )
  }

  const topRate = rates.length ? rates[0].rate_per_100 : null

  return (
    <>
      <PageHeader title="Rewards" subtitle={`${cards.length} cards · ${summary.length} statement months tracked`} />

      {/* Section 1 — Points Overview */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {rates.map((r) => (
          <div key={r.card} className="rounded-xl border border-border bg-card p-5 shadow-card">
            <div className="font-sora text-sm font-600 text-text">{r.card}</div>
            <div className="mt-2 font-data text-3xl font-500 text-accent">
              {formatINR(r.latest_balance)}
            </div>
            <div className="font-data text-xs text-muted">points balance</div>
            <div className="mt-3 flex justify-between font-data text-xs">
              <span className="text-muted">
                +{formatINR(latestEarned[r.card]?.earned || 0)} this month
              </span>
              <span className="text-green">{r.rate_per_100} / ₹100</span>
            </div>
          </div>
        ))}
      </div>

      {/* Section 2 — Points Trend */}
      <Card title="Points Balance Trend" className="mt-6">
        <ResponsiveContainer width="100%" height={340}>
          <LineChart data={trend}>
            <CartesianGrid stroke="#1f2937" />
            <XAxis dataKey="month" tick={{ fontSize: 11, fill: '#64748b', fontFamily: 'DM Mono, monospace' }} />
            <YAxis tick={{ fontSize: 11, fill: '#64748b', fontFamily: 'DM Mono, monospace' }} tickFormatter={(v) => formatINR(v)} />
            <Tooltip
              contentStyle={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 8, fontFamily: 'DM Mono, monospace', fontSize: 12 }}
              labelStyle={{ color: '#64748b' }}
            />
            <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'Sora, sans-serif' }} />
            {cards.map((c, i) => (
              <Line key={c} type="monotone" dataKey={c} stroke={colorFor(c, i)} strokeWidth={2} dot={false} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Card>

      {/* Section 3 — Reward Rates */}
      <Card title="Reward Rates" className="mt-6">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-border">
                <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">Card</th>
                <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">Points Earned</th>
                <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">Total Spend</th>
                <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">Rate (pts/₹100)</th>
                <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">Latest Balance</th>
              </tr>
            </thead>
            <tbody>
              {rates.map((r) => {
                const isTop = topRate != null && r.rate_per_100 === topRate && r.rate_per_100 > 0
                return (
                  <tr key={r.card} className={`border-b border-border/50 ${isTop ? 'bg-accent/10' : ''}`}>
                    <td className="px-3 py-2 text-left font-sora text-sm text-text">{r.card}</td>
                    <td className="px-3 py-2 text-right text-sm text-text">{formatINR(r.total_points)}</td>
                    <td className="px-3 py-2 text-right text-sm text-muted">₹{formatINR(r.total_spend)}</td>
                    <td className={`px-3 py-2 text-right text-sm ${isTop ? 'text-accent' : 'text-text'}`}>{r.rate_per_100}</td>
                    <td className="px-3 py-2 text-right text-sm text-muted">{formatINR(r.latest_balance)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Section 4 — Category Optimizer */}
      <Card title="Category Optimizer" className="mt-6">
        {optimize.length === 0 ? (
          <p className="font-data text-sm text-muted">
            Parse more statements to unlock category optimization.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-border">
                  <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">Category</th>
                  <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">Best Card</th>
                  <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">Rate (pts/₹100)</th>
                  <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">All Cards Ranked</th>
                </tr>
              </thead>
              <tbody>
                {optimize.map((o) => (
                  <tr key={o.category} className="border-b border-border/50">
                    <td className="px-3 py-2 text-left font-sora text-sm text-text">{o.category}</td>
                    <td className="px-3 py-2 text-left text-sm text-accent">{o.best_card}</td>
                    <td className="px-3 py-2 text-right text-sm text-text">{o.rate_per_100}</td>
                    <td className="px-3 py-2 text-left font-data text-xs text-muted">
                      {o.all_cards.map((c) => `${c.card} (${c.rate})`).join(' · ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}
