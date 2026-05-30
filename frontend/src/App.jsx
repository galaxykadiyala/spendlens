import { Routes, Route } from 'react-router-dom'
import NavBar from './components/NavBar'
import Overview from './pages/Overview'
import Categories from './pages/Categories'
import Monthly from './pages/Monthly'
import Cards from './pages/Cards'
import Merchants from './pages/Merchants'
import Transactions from './pages/Transactions'
import Insights from './pages/Insights'
import Review from './pages/Review'
import Rewards from './pages/Rewards'

export default function App() {
  return (
    <div className="min-h-screen">
      <NavBar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/categories" element={<Categories />} />
          <Route path="/monthly" element={<Monthly />} />
          <Route path="/cards" element={<Cards />} />
          <Route path="/merchants" element={<Merchants />} />
          <Route path="/rewards" element={<Rewards />} />
          <Route path="/transactions" element={<Transactions />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="/review" element={<Review />} />
        </Routes>
      </main>
    </div>
  )
}
