import { useEffect, useState } from 'react'
import { api } from '../api'
import { PageHeader, Loading, EmptyState, InsightCard } from '../components/Common'

export default function Insights() {
  const [insights, setInsights] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.insights().then(setInsights).finally(() => setLoading(false))
  }, [])

  if (loading) return <Loading />

  const counts = insights.reduce(
    (acc, i) => {
      acc[i.level] = (acc[i.level] || 0) + 1
      return acc
    },
    { red: 0, amber: 0, green: 0 }
  )

  return (
    <>
      <PageHeader
        title="Insights"
        subtitle={`${counts.red} alerts · ${counts.amber} watch · ${counts.green} good`}
      />
      {insights.length === 0 ? (
        <EmptyState message="No insights yet. Parse statements to generate insights." />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {insights.map((ins, i) => (
            <InsightCard key={i} insight={ins} />
          ))}
        </div>
      )}
    </>
  )
}
