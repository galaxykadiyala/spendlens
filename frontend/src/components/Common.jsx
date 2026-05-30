export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="font-sora text-2xl font-700 tracking-tight text-text">{title}</h1>
        {subtitle && <p className="mt-1 font-data text-sm text-muted">{subtitle}</p>}
      </div>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  )
}

export function Card({ title, children, className = '' }) {
  return (
    <div className={`rounded-xl border border-border bg-card p-5 shadow-card ${className}`}>
      {title && (
        <h2 className="mb-4 font-sora text-sm font-600 uppercase tracking-wider text-muted">
          {title}
        </h2>
      )}
      {children}
    </div>
  )
}

export function Loading() {
  return (
    <div className="flex h-64 items-center justify-center font-data text-sm text-muted">
      Loading…
    </div>
  )
}

export function EmptyState({ message = 'No data yet. Parse some statements first.' }) {
  return (
    <div className="flex h-64 flex-col items-center justify-center gap-2 text-center">
      <span className="text-3xl">📭</span>
      <p className="font-data text-sm text-muted">{message}</p>
    </div>
  )
}

export function InsightCard({ insight }) {
  const border = {
    red: 'border-red/60',
    amber: 'border-accent/60',
    green: 'border-green/60',
  }[insight.level] || 'border-border'
  return (
    <div className={`rounded-xl border-l-4 ${border} border border-border bg-card p-5 shadow-card`}>
      <p className="font-sora text-sm text-text">{insight.message}</p>
      {insight.amount != null && (
        <p className="mt-2 font-data text-2xl font-500 text-text">
          ₹{Number(insight.amount).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
        </p>
      )}
    </div>
  )
}

export function CategoryBadge({ category, color }) {
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 font-sora text-xs"
      style={{ background: `${color}22`, color }}
    >
      {category}
    </span>
  )
}
