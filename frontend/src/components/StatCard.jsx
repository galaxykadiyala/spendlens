export default function StatCard({ label, value, sub, accent = 'text', prefix = '' }) {
  const accentClass = {
    text: 'text-text',
    accent: 'text-accent',
    green: 'text-green',
    red: 'text-red',
  }[accent]

  return (
    <div className="rounded-xl border border-border bg-card p-5 shadow-card">
      <div className="font-sora text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-2 font-data text-3xl font-500 ${accentClass}`}>
        {prefix}
        {value}
      </div>
      {sub && <div className="mt-1 font-data text-xs text-muted">{sub}</div>}
    </div>
  )
}
