import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { api } from '../api'

const LINKS = [
  { to: '/', label: 'Overview', end: true },
  { to: '/categories', label: 'Categories' },
  { to: '/monthly', label: 'Monthly' },
  { to: '/cards', label: 'Cards' },
  { to: '/merchants', label: 'Merchants' },
  { to: '/rewards', label: 'Rewards' },
  { to: '/transactions', label: 'Transactions' },
  { to: '/insights', label: 'Insights' },
  { to: '/review', label: 'Review Queue' },
]

export default function NavBar() {
  const [reviewCount, setReviewCount] = useState(0)
  const location = useLocation()

  // Refresh the badge on mount and whenever the route changes (e.g. after
  // confirming items in the queue).
  useEffect(() => {
    let cancelled = false
    api
      .reviewQueue()
      .then((d) => {
        if (!cancelled) setReviewCount(d.total || 0)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [location.pathname])

  return (
    <nav className="sticky top-0 z-20 border-b border-border bg-bg/90 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-4">
        <div className="flex items-center gap-2">
          <span className="text-2xl">💳</span>
          <span className="font-sora text-lg font-700 tracking-tight text-text">
            Spend<span className="text-accent">Lens</span>
          </span>
        </div>
        <div className="flex flex-wrap gap-1">
          {LINKS.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) =>
                `relative rounded-md px-3 py-1.5 text-sm font-sora transition-colors ${
                  isActive
                    ? 'bg-accent/15 text-accent'
                    : 'text-muted hover:bg-card hover:text-text'
                }`
              }
            >
              {l.label}
              {l.to === '/review' && reviewCount > 0 && (
                <span className="ml-1.5 inline-flex min-w-[18px] items-center justify-center rounded-full bg-red px-1.5 py-0.5 font-data text-[10px] font-500 leading-none text-white">
                  {reviewCount}
                </span>
              )}
            </NavLink>
          ))}
        </div>
      </div>
    </nav>
  )
}
