import {
  PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  AreaChart, Area, Line, ComposedChart,
} from 'recharts'
import { colorFor, formatINR } from '../api'

const AXIS = { fontSize: 11, fill: '#64748b', fontFamily: 'DM Mono, monospace' }
const GRID = '#1f2937'

function TooltipBox({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2 shadow-card">
      {label && <div className="mb-1 font-sora text-xs text-muted">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 font-data text-xs text-text">
          <span className="inline-block h-2 w-2 rounded-sm" style={{ background: p.color || p.fill }} />
          <span className="text-muted">{p.name}:</span>
          <span>₹{formatINR(p.value)}</span>
        </div>
      ))}
    </div>
  )
}

export function DonutChart({ data, height = 280 }) {
  // data: [{ name, value }]
  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          innerRadius="55%"
          outerRadius="80%"
          paddingAngle={2}
          stroke="#0a0e1a"
        >
          {data.map((d, i) => (
            <Cell key={i} fill={colorFor(d.name, i)} />
          ))}
        </Pie>
        <Tooltip content={<TooltipBox />} />
        <Legend
          wrapperStyle={{ fontSize: 11, fontFamily: 'Sora, sans-serif', color: '#64748b' }}
        />
      </PieChart>
    </ResponsiveContainer>
  )
}

export function HorizontalBarChart({ data, dataKey = 'total', nameKey = 'name', height = 320 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ left: 20, right: 20 }}>
        <CartesianGrid stroke={GRID} horizontal={false} />
        <XAxis type="number" tick={AXIS} tickFormatter={(v) => `₹${formatINR(v)}`} />
        <YAxis type="category" dataKey={nameKey} tick={AXIS} width={130} />
        <Tooltip content={<TooltipBox />} cursor={{ fill: 'rgba(245,158,11,0.08)' }} />
        <Bar dataKey={dataKey} radius={[0, 4, 4, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={colorFor(d[nameKey], i)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

export function StackedAreaChart({ data, categories, onMonthClick, height = 380, xTickFormatter }) {
  // data: [{ month, <cat>: total, ... , __total }]. xTickFormatter is DISPLAY-only;
  // onMonthClick still receives the raw month value (e.activeLabel).
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={data}
        onClick={(e) => {
          if (onMonthClick && e && e.activeLabel) onMonthClick(e.activeLabel)
        }}
      >
        <CartesianGrid stroke={GRID} />
        <XAxis dataKey="month" tick={AXIS} tickFormatter={xTickFormatter} />
        <YAxis tick={AXIS} tickFormatter={(v) => `₹${formatINR(v)}`} />
        <Tooltip content={<TooltipBox />} />
        {categories.map((cat, i) => (
          <Area
            key={cat}
            type="monotone"
            dataKey={cat}
            stackId="1"
            stroke={colorFor(cat, i)}
            fill={colorFor(cat, i)}
            fillOpacity={0.5}
          />
        ))}
        <Line
          type="monotone"
          dataKey="__total"
          name="Total"
          stroke="#f59e0b"
          strokeWidth={2}
          dot={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

export function IncomeSpendBar({ income, spend, height = 80 }) {
  const saved = Math.max(income - spend, 0)
  const data = [{ name: 'Monthly', Saved: saved, Spent: spend }]
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ left: 0, right: 0, top: 10, bottom: 10 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="name" hide />
        <Tooltip content={<TooltipBox />} cursor={false} />
        <Bar dataKey="Spent" stackId="a" fill="#f59e0b" radius={[4, 0, 0, 4]} />
        <Bar dataKey="Saved" stackId="a" fill="#10b981" radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}
