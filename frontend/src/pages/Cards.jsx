import { useEffect, useState } from 'react'
import { api, formatINR } from '../api'
import { Card, PageHeader, Loading, EmptyState } from '../components/Common'
import { HorizontalBarChart, DonutChart } from '../components/Charts'

export default function Cards() {
  const [cards, setCards] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.cards().then(setCards).finally(() => setLoading(false))
  }, [])

  if (loading) return <Loading />
  if (!cards.length) return (<><PageHeader title="Cards" /><EmptyState /></>)

  const barData = cards.map((c) => ({ name: c.card, total: c.total }))

  return (
    <>
      <PageHeader title="Cards" subtitle={`${cards.length} cards`} />

      <Card title="Spend by Card">
        <HorizontalBarChart data={barData} dataKey="total" nameKey="name" height={Math.max(220, cards.length * 48)} />
      </Card>

      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {cards.map((c) => (
          <Card key={c.card} title={c.card}>
            <div className="mb-3 flex items-baseline justify-between">
              <span className="font-data text-2xl text-text">₹{formatINR(c.total)}</span>
              <span className="font-data text-xs text-muted">{c.pct}% of spend</span>
            </div>
            <DonutChart
              data={c.categories.slice(0, 6).map((x) => ({ name: x.category, value: x.total }))}
              height={220}
            />
          </Card>
        ))}
      </div>
    </>
  )
}
