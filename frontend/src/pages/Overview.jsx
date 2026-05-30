import { useEffect, useState } from 'react'
import { api, formatINR } from '../api'
import StatCard from '../components/StatCard'
import { Card, PageHeader, Loading, EmptyState, InsightCard } from '../components/Common'
import { DonutChart, IncomeSpendBar } from '../components/Charts'

export default function Overview() {
  const [summary, setSummary] = useState(null)
  const [categories, setCategories] = useState([])
  const [insights, setInsights] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([api.summary(), api.categories(), api.insights()])
      .then(([s, c, i]) => {
        setSummary(s)
        setCategories(c)
        setInsights(i)
      })
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <Loading />
  if (!summary || summary.transaction_count === 0) {
    return (
      <>
        <PageHeader title="Overview" />
        <EmptyState />
      </>
    )
  }

  const top6 = categories.slice(0, 6).map((c) => ({ name: c.category, value: c.total }))

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle={
          summary.date_range.start
            ? `${summary.date_range.start} → ${summary.date_range.end} · ${summary.num_months} months · ${summary.card_count} cards`
            : null
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Total Spend" prefix="₹" value={formatINR(summary.total_spend)} sub={`${summary.transaction_count} transactions`} accent="accent" />
        <StatCard label="Monthly Avg" prefix="₹" value={formatINR(summary.monthly_avg)} sub={`over ${summary.num_months} months`} />
        <StatCard
          label="Savings Rate"
          value={`${summary.savings_rate}%`}
          sub={`income ₹${formatINR(summary.monthly_income)}/mo`}
          accent={summary.savings_rate >= 40 ? 'green' : summary.savings_rate < 0 ? 'red' : 'text'}
        />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Income vs Spend (monthly avg)">
          <IncomeSpendBar income={summary.monthly_income} spend={summary.monthly_avg} />
          <div className="mt-3 flex justify-between font-data text-xs">
            <span className="text-accent">Spent ₹{formatINR(summary.monthly_avg)}</span>
            <span className="text-green">
              Saved ₹{formatINR(Math.max(summary.monthly_income - summary.monthly_avg, 0))}
            </span>
          </div>
        </Card>

        <Card title="Top 6 Categories">
          <DonutChart data={top6} />
        </Card>
      </div>

      <h2 className="mb-3 mt-8 font-sora text-sm font-600 uppercase tracking-wider text-muted">
        Top Insights
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
