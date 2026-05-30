import { useEffect, useMemo, useState } from 'react'
import { api, formatINR, CATEGORIES } from '../api'
import { PageHeader, Loading } from '../components/Common'

export default function Review() {
  const [queue, setQueue] = useState([])
  const [loading, setLoading] = useState(true)
  // Per-id chosen category in the dropdowns.
  const [choices, setChoices] = useState({})
  // Ids currently animating out after confirmation.
  const [leaving, setLeaving] = useState({})
  // Ids fully removed from view (confirmed).
  const [confirmed, setConfirmed] = useState({})
  const [saving, setSaving] = useState(false)

  const load = () => {
    setLoading(true)
    api
      .reviewQueue()
      .then((d) => {
        setQueue(d.transactions)
        // Pre-select the suggestion if present, else keep the current category
        // (so "Save All" without edits is a safe no-op, not a mass-relabel).
        const init = {}
        d.transactions.forEach((t) => {
          init[t.id] =
            t.suggested_category && CATEGORIES.includes(t.suggested_category)
              ? t.suggested_category
              : CATEGORIES.includes(t.category)
              ? t.category
              : 'Miscellaneous'
        })
        setChoices(init)
        setConfirmed({})
        setLeaving({})
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  const visible = useMemo(
    () => queue.filter((t) => !confirmed[t.id]),
    [queue, confirmed]
  )

  const total = queue.length
  const reviewedCount = Object.keys(confirmed).length
  const pct = total ? Math.round((reviewedCount / total) * 100) : 0

  const setChoice = (id, cat) => setChoices((c) => ({ ...c, [id]: cat }))

  // Animate a card out, then mark it confirmed (removed from the list).
  const animateOut = (ids) => {
    const mark = {}
    ids.forEach((id) => (mark[id] = true))
    setLeaving((l) => ({ ...l, ...mark }))
    setTimeout(() => {
      setConfirmed((c) => ({ ...c, ...mark }))
    }, 300)
  }

  const confirmOne = async (id) => {
    const category = choices[id]
    if (!category) return
    try {
      await api.bulkCategorize([{ id, category }])
      animateOut([id])
    } catch (e) {
      // Leave the card in place on failure.
    }
  }

  const saveAll = async () => {
    const items = visible.map((t) => ({ id: t.id, category: choices[t.id] }))
    if (!items.length) return
    setSaving(true)
    try {
      await api.bulkCategorize(items)
      animateOut(items.map((i) => i.id))
    } catch (e) {
      // no-op
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <Loading />

  // Empty / all-done state.
  if (total === 0 || visible.length === 0) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
        <div className="flex h-24 w-24 items-center justify-center rounded-full bg-green/15 text-5xl">
          ✅
        </div>
        <h2 className="font-sora text-2xl font-700 text-green">All transactions categorized</h2>
        <p className="font-data text-sm text-muted">Nothing in the review queue. Nice work.</p>
      </div>
    )
  }

  return (
    <div className="pb-28">
      <PageHeader title={`Review Queue — ${visible.length} transactions need categorization`} />

      {/* Progress bar */}
      <div className="mb-6">
        <div className="mb-1 flex justify-between font-data text-xs text-muted">
          <span>
            {reviewedCount} of {total} reviewed
          </span>
          <span>{pct}%</span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-border">
          <div
            className="h-full rounded-full bg-green transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3">
        {visible.map((t) => {
          const isLeaving = leaving[t.id]
          return (
            <div
              key={t.id}
              className={`rounded-xl border border-border bg-card p-4 shadow-card transition-all duration-300 ${
                isLeaving ? 'translate-x-8 opacity-0' : 'translate-x-0 opacity-100'
              }`}
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="font-sora text-base font-600 text-text">{t.description}</div>
                  <div className="mt-0.5 font-data text-xs text-muted">
                    {t.date} · {t.card}
                    {t.suggested_category ? (
                      <span className="ml-2 text-accent">suggested: {t.suggested_category} ({Math.round((t.confidence || 0) * 100)}%)</span>
                    ) : null}
                  </div>
                </div>
                <div className="whitespace-nowrap font-data text-lg font-500 text-accent">
                  ₹{formatINR(t.amount)}
                </div>
              </div>

              <div className="mt-3 flex items-center gap-3">
                <select
                  value={choices[t.id] || 'Miscellaneous'}
                  onChange={(e) => setChoice(t.id, e.target.value)}
                  className="flex-1 rounded-md border border-border bg-bg px-3 py-2 font-sora text-sm text-text"
                >
                  {CATEGORIES.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => confirmOne(t.id)}
                  className="rounded-md border border-green/50 bg-green/10 px-4 py-2 font-sora text-sm text-green hover:bg-green/20"
                >
                  ✓ Confirm
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {/* Sticky Save All */}
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-bg/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
          <span className="font-data text-sm text-muted">
            {visible.length} pending · selections ready to save
          </span>
          <button
            onClick={saveAll}
            disabled={saving || visible.length === 0}
            className="rounded-md border border-accent/50 bg-accent/15 px-6 py-2 font-sora text-sm font-600 text-accent hover:bg-accent/25 disabled:opacity-40"
          >
            {saving ? 'Saving…' : `Save All (${visible.length})`}
          </button>
        </div>
      </div>
    </div>
  )
}
