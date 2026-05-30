import { useEffect, useState } from 'react'
import { api, formatINR, colorFor } from '../api'
import { Card, PageHeader, Loading, EmptyState, CategoryBadge } from '../components/Common'

export default function Merchants() {
  const [merchants, setMerchants] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.merchants().then(setMerchants).finally(() => setLoading(false))
  }, [])

  if (loading) return <Loading />
  if (!merchants.length) return (<><PageHeader title="Merchants" /><EmptyState /></>)

  return (
    <>
      <PageHeader title="Top Merchants" subtitle="Top 20 by total spend" />
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-border">
                <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">#</th>
                <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">Merchant</th>
                <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">Category</th>
                <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">Total ₹</th>
                <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">Txns</th>
                <th className="px-3 py-2 text-right text-xs uppercase tracking-wider text-muted">Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {merchants.map((m, i) => (
                <tr key={m.merchant} className="border-b border-border/50 hover:bg-bg/40">
                  <td className="px-3 py-2 text-left text-sm text-muted">{i + 1}</td>
                  <td className="px-3 py-2 text-left font-sora text-sm text-text">{m.merchant}</td>
                  <td className="px-3 py-2 text-left text-sm">
                    <CategoryBadge category={m.category} color={colorFor(m.category)} />
                  </td>
                  <td className="px-3 py-2 text-right text-sm text-text">₹{formatINR(m.total)}</td>
                  <td className="px-3 py-2 text-right text-sm text-muted">{m.count}</td>
                  <td className="px-3 py-2 text-right text-sm text-muted">{m.last_seen}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  )
}
