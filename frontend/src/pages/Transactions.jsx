import { useEffect, useMemo, useState } from 'react'
import { api, formatINR, colorFor, CATEGORIES } from '../api'
import { Card, PageHeader, Loading, EmptyState } from '../components/Common'

export default function Transactions() {
  const [data, setData] = useState({ transactions: [], total: 0, pages: 1 })
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)

  // Filters
  const [q, setQ] = useState('')
  const [card, setCard] = useState('')
  const [category, setCategory] = useState('')
  const [month, setMonth] = useState('')
  const [maxRange, setMaxRange] = useState(100000)
  const [amountCap, setAmountCap] = useState(100000)
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState('date')
  const [dir, setDir] = useState('DESC')
  const [showExcluded, setShowExcluded] = useState(false)
  const [toast, setToast] = useState('')

  const perPage = 50

  const showToast = (msg) => {
    setToast(msg)
    setTimeout(() => setToast(''), 3500)
  }

  const postJson = (url, body) =>
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    }).then((r) => r.json())

  // Load summary once for filter option lists + amount slider bounds.
  useEffect(() => {
    api.summary().then((s) => {
      setSummary(s)
    })
  }, [])

  // Determine a sensible slider max from the largest transaction (loaded lazily).
  useEffect(() => {
    api.transactions({ per_page: 1, sort: 'amount', dir: 'DESC' }).then((d) => {
      const top = d.transactions[0]?.amount || 100000
      const ceiling = Math.ceil(top / 1000) * 1000 || 100000
      setMaxRange(ceiling)
      setAmountCap(ceiling)
    })
  }, [])

  const cards = summary?.cards || []

  const load = () => {
    setLoading(true)
    const params = { page, per_page: perPage, sort, dir }
    if (q) params.q = q
    if (card) params.card = card
    if (category) params.category = category
    if (month) params.month = month
    if (amountCap < maxRange) params.max_amount = amountCap
    if (!showExcluded) params.excluded = 0 // hide excluded unless toggled on
    api
      .transactions(params)
      .then(setData)
      .finally(() => setLoading(false))
  }

  // Reload on any filter/sort/page change (debounced for search text).
  useEffect(() => {
    const id = setTimeout(load, 250)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, card, category, month, amountCap, page, sort, dir, showExcluded])

  // Reset to page 1 when filters change.
  useEffect(() => {
    setPage(1)
  }, [q, card, category, month, amountCap, sort, dir, showExcluded])

  const months = summary?.months || []
  const cardOptions = useMemo(() => {
    // Derive cards from current page if summary lacks them.
    const set = new Set(data.transactions.map((t) => t.card).filter(Boolean))
    return [...set].sort()
  }, [data])

  const toggleSort = (key) => {
    if (sort === key) setDir(dir === 'ASC' ? 'DESC' : 'ASC')
    else {
      setSort(key)
      setDir('DESC')
    }
  }

  const onRecategorize = (id, newCat) => {
    api.recategorize({ id, category: newCat }).then(() => load())
  }

  const onToggleExclude = (t) => {
    postJson(`/api/transactions/${t.id}/exclude`, { excluded: !t.excluded }).then(() => load())
  }

  const onAutoMatch = () => {
    postJson('/api/transactions/auto-exclude-refunds').then((d) => {
      showToast(`${d.pairs_found || 0} refund pairs excluded`)
      load()
    })
  }

  const Th = ({ label, k, right }) => (
    <th
      onClick={() => toggleSort(k)}
      className={`cursor-pointer select-none px-3 py-2 text-xs uppercase tracking-wider text-muted hover:text-text ${right ? 'text-right' : 'text-left'}`}
    >
      {label} {sort === k ? (dir === 'ASC' ? '▲' : '▼') : ''}
    </th>
  )

  return (
    <>
      <PageHeader title="Transactions" subtitle={`${data.total} matching`}>
        <button
          onClick={onAutoMatch}
          className="rounded-md border border-border bg-card px-3 py-1.5 font-sora text-sm text-muted hover:bg-card hover:text-text"
        >
          ⟲ Auto-match refunds
        </button>
        <a
          href={api.exportCsvUrl()}
          className="rounded-md border border-border bg-card px-3 py-1.5 font-sora text-sm text-accent hover:bg-accent/10"
        >
          ⬇ Export CSV
        </a>
      </PageHeader>

      {toast && (
        <div className="mb-4 rounded-md border border-green/50 bg-green/10 px-4 py-2 font-data text-sm text-green">
          {toast}
        </div>
      )}

      <Card className="mb-4">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search description…"
            className="rounded-md border border-border bg-bg px-3 py-2 font-data text-sm text-text placeholder:text-muted"
          />
          <select
            value={card}
            onChange={(e) => setCard(e.target.value)}
            className="rounded-md border border-border bg-bg px-3 py-2 font-data text-sm text-text"
          >
            <option value="">All cards</option>
            {(cards.length ? cards : cardOptions).map((c) => (<option key={c} value={c}>{c}</option>))}
          </select>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="rounded-md border border-border bg-bg px-3 py-2 font-data text-sm text-text"
          >
            <option value="">All categories</option>
            {CATEGORIES.map((c) => (<option key={c} value={c}>{c}</option>))}
          </select>
          <select
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="rounded-md border border-border bg-bg px-3 py-2 font-data text-sm text-text"
          >
            <option value="">All months</option>
            {months.map((m) => (<option key={m} value={m}>{m}</option>))}
          </select>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <label className="font-data text-xs text-muted">Max amount</label>
          <input
            type="range"
            min={0}
            max={maxRange}
            step={500}
            value={amountCap}
            onChange={(e) => setAmountCap(Number(e.target.value))}
            className="flex-1"
          />
          <span className="font-data text-sm text-accent">≤ ₹{formatINR(amountCap)}</span>
          <label className="ml-4 flex items-center gap-2 font-data text-xs text-muted">
            <input
              type="checkbox"
              checked={showExcluded}
              onChange={(e) => setShowExcluded(e.target.checked)}
            />
            Show excluded
          </label>
        </div>
      </Card>

      <Card>
        {loading ? (
          <Loading />
        ) : data.transactions.length === 0 ? (
          <EmptyState message="No transactions match these filters." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-border">
                  <Th label="Date" k="date" />
                  <Th label="Description" k="description" />
                  <Th label="Amount" k="amount" right />
                  <Th label="Card" k="card" />
                  <th className="px-3 py-2 text-left text-xs uppercase tracking-wider text-muted">Category</th>
                  <th className="px-3 py-2 text-center text-xs uppercase tracking-wider text-muted">Excl</th>
                </tr>
              </thead>
              <tbody>
                {data.transactions.map((t) => {
                  const ex = !!t.excluded
                  return (
                  <tr
                    key={t.id}
                    className={`border-b border-border/50 hover:bg-bg/40 ${ex ? 'opacity-50 line-through' : ''}`}
                  >
                    <td className="px-3 py-2 text-left text-sm text-muted">{t.date}</td>
                    <td className="px-3 py-2 text-left font-sora text-sm text-text">{t.description}</td>
                    <td className="px-3 py-2 text-right text-sm text-text">₹{formatINR(t.amount)}</td>
                    <td className="px-3 py-2 text-left text-sm text-muted">{t.card}</td>
                    <td className="px-3 py-2 text-left text-sm">
                      <select
                        value={CATEGORIES.includes(t.category) ? t.category : 'Miscellaneous'}
                        onChange={(e) => onRecategorize(t.id, e.target.value)}
                        className="rounded-md border border-border bg-bg px-2 py-1 font-sora text-xs no-underline"
                        style={{ color: colorFor(t.category) }}
                      >
                        {CATEGORIES.map((c) => (<option key={c} value={c}>{c}</option>))}
                      </select>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <button
                        onClick={() => onToggleExclude(t)}
                        title={ex ? 'Excluded — click to include' : 'Click to exclude from totals'}
                        className={`text-base leading-none ${ex ? 'text-red' : 'text-muted hover:text-text'}`}
                      >
                        {ex ? '●' : '○'}
                      </button>
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {data.pages > 1 && (
          <div className="mt-4 flex items-center justify-between font-data text-sm text-muted">
            <button
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="rounded-md border border-border px-3 py-1 disabled:opacity-40 hover:text-text"
            >
              ← Prev
            </button>
            <span>
              Page {page} / {data.pages}
            </span>
            <button
              disabled={page >= data.pages}
              onClick={() => setPage((p) => Math.min(data.pages, p + 1))}
              className="rounded-md border border-border px-3 py-1 disabled:opacity-40 hover:text-text"
            >
              Next →
            </button>
          </div>
        )}
      </Card>
    </>
  )
}
