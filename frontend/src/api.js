import axios from 'axios'

// Relative base — Vite proxies /api to the FastAPI backend in dev.
const client = axios.create({ baseURL: '/' })

export const api = {
  summary: () => client.get('/api/summary').then((r) => r.data),
  transactions: (params) => client.get('/api/transactions', { params }).then((r) => r.data),
  categories: () => client.get('/api/categories').then((r) => r.data),
  monthly: () => client.get('/api/monthly').then((r) => r.data),
  merchants: () => client.get('/api/merchants').then((r) => r.data),
  cards: () => client.get('/api/cards').then((r) => r.data),
  insights: () => client.get('/api/insights').then((r) => r.data),
  recategorize: (body) => client.post('/api/recategorize', body).then((r) => r.data),
  reviewQueue: () => client.get('/api/review-queue').then((r) => r.data),
  bulkCategorize: (items) => client.post('/api/bulk-categorize', items).then((r) => r.data),
  rewards: {
    summary: () => client.get('/api/rewards/summary').then((r) => r.data),
    rates: () => client.get('/api/rewards/rates').then((r) => r.data),
    optimize: () => client.get('/api/rewards/optimize').then((r) => r.data),
  },
  parse: () => client.post('/api/parse').then((r) => r.data),
  exportCsvUrl: () => '/api/export/csv',
}

// Shared category palette (stable colors across charts).
export const CATEGORY_COLORS = {
  Fuel: '#f59e0b',
  Groceries: '#10b981',
  'Food Delivery': '#ef4444',
  'Food & Dining': '#fb7185',
  Medical: '#38bdf8',
  Education: '#a78bfa',
  Shopping: '#f472b6',
  'Health & Wellness': '#34d399',
  Transport: '#facc15',
  'Bills & Utilities': '#60a5fa',
  Subscriptions: '#c084fc',
  'Personal Care': '#fda4af',
  'Household Help': '#fbbf24',
  Entertainment: '#22d3ee',
  Kids: '#4ade80',
  Insurance: '#818cf8',
  Investments: '#2dd4bf',
  'Fees & Charges': '#f87171',
  Miscellaneous: '#64748b',
}

// Full category list (mirrors backend categorizer rules) for inline editing.
export const CATEGORIES = [
  'Fuel', 'Groceries', 'Food Delivery', 'Food & Dining', 'Medical', 'Education',
  'Shopping', 'Health & Wellness', 'Transport', 'Bills & Utilities', 'Subscriptions',
  'Personal Care', 'Household Help', 'Entertainment', 'Kids', 'Insurance',
  'Investments', 'Fees & Charges', 'Miscellaneous',
]

export const PALETTE = [
  '#f59e0b', '#10b981', '#38bdf8', '#a78bfa', '#f472b6', '#facc15',
  '#60a5fa', '#c084fc', '#34d399', '#22d3ee', '#fb7185', '#818cf8',
]

export function colorFor(category, i = 0) {
  return CATEGORY_COLORS[category] || PALETTE[i % PALETTE.length]
}

export function formatINR(n, withDecimals = false) {
  const num = Number(n || 0)
  return num.toLocaleString('en-IN', {
    maximumFractionDigits: withDecimals ? 2 : 0,
    minimumFractionDigits: withDecimals ? 2 : 0,
  })
}
