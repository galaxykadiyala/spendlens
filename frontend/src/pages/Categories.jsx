import { useEffect, useMemo, useState } from 'react'
import { api, formatINR } from '../api'
import { Card, PageHeader, Loading, EmptyState } from '../components/Common'
import { DonutChart } from '../components/Charts'

export default function Categories() {
  const [cats, setCats] = useState([])
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState('total')
  const [sortDir, setSortDir] = useState('desc')

  useEffect(() => {
    api.categories().then(setCats).finally(() => setLoading(false))
  }, [])

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
  if (!cats.length) return (<><PageHeader title="Categories" /><EmptyState /></>)

  const donut = cats.slice(0, 8).map((c) => ({ name: c.category, value: c.total }))

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
      <PageHeader title="Categories" subtitle={`${cats.length} categories`} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <Card title="Spend Split" className="lg:col-span-2">
          <DonutChart data={donut} height={320} />
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
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </>
  )
}
