import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { pythonStrategyApi } from '@/api/python-strategy'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { usePythonStrategyBacktestStore } from '@/stores/pythonStrategyBacktestStore'
import { useAuthStore } from '@/stores/authStore'
import { Loader2 } from 'lucide-react'
import { showToast } from '@/utils/toast'

export default function PythonStrategyBacktester() {
  const navigate = useNavigate()
  const { apiKey } = useAuthStore()
  const { setResult, setLoading, setError, isLoading } = usePythonStrategyBacktestStore()
  
  const [strategies, setStrategies] = useState<any[]>([])
  
  const [formData, setFormData] = useState({
    strategy_id: '',
    symbols: '', // Comma separated for now
    interval: '15m',
    lookback_days: 60,
    initial_capital: 100000,
    source: 'db',
  })

  useEffect(() => {
    pythonStrategyApi.getStrategies().then(setStrategies).catch(console.error)
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (!formData.strategy_id || !formData.symbols) {
      showToast.error('Please select a strategy and symbols.')
      return
    }

    setLoading(true)
    setError(null)
    
    try {
      const symbolsList = formData.symbols.split(',').map(s => s.trim()).filter(Boolean)
      
      const payload = {
        strategy_id: formData.strategy_id,
        symbols: symbolsList,
        interval: formData.interval,
        lookback_days: Number(formData.lookback_days),
        initial_capital: Number(formData.initial_capital),
        source: formData.source,
        apikey: apiKey || ''
      }
      
      const res = await pythonStrategyApi.runBacktest(payload)
      
      if (res.status === 'success') {
        setResult(res)
        navigate('/tools/python-backtester/results')
      } else {
        throw new Error(res.message || 'Backtest failed')
      }
    } catch (err: any) {
      console.error(err)
      const errorMsg = err.response?.data?.message || err.message || 'An unknown error occurred'
      setError(errorMsg)
      showToast.error(errorMsg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="container py-8 max-w-4xl mx-auto space-y-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold tracking-tight">Python Strategy Backtester</h1>
        <p className="text-muted-foreground">
          Run high-performance VectorBT backtests on your custom Python scripts.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Configure Backtest</CardTitle>
          <CardDescription>Select a strategy and define the historical parameters.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-6">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="strategy">Select Strategy</Label>
                <Select
                  value={formData.strategy_id}
                  onValueChange={(val) => setFormData(prev => ({ ...prev, strategy_id: val }))}
                >
                  <SelectTrigger id="strategy">
                    <SelectValue placeholder="Select a Python strategy..." />
                  </SelectTrigger>
                  <SelectContent>
                    {strategies.map((st) => (
                      <SelectItem key={st.id} value={st.id}>
                        {st.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="symbols">Symbols (Comma separated)</Label>
                <Input 
                  id="symbols" 
                  placeholder="e.g. SBIN, RELIANCE, NIFTY"
                  value={formData.symbols}
                  onChange={(e) => setFormData(prev => ({ ...prev, symbols: e.target.value.toUpperCase() }))}
                  required
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="interval">Timeframe</Label>
                <Select
                  value={formData.interval}
                  onValueChange={(val) => setFormData(prev => ({ ...prev, interval: val }))}
                >
                  <SelectTrigger id="interval">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="1m">1 Minute</SelectItem>
                    <SelectItem value="3m">3 Minutes</SelectItem>
                    <SelectItem value="5m">5 Minutes</SelectItem>
                    <SelectItem value="15m">15 Minutes</SelectItem>
                    <SelectItem value="30m">30 Minutes</SelectItem>
                    <SelectItem value="1h">1 Hour</SelectItem>
                    <SelectItem value="1d">1 Day</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="lookback">Data Lookback (Days)</Label>
                <Input 
                  id="lookback" 
                  type="number"
                  min={1}
                  max={3650}
                  value={formData.lookback_days}
                  onChange={(e) => setFormData(prev => ({ ...prev, lookback_days: parseInt(e.target.value) }))}
                  required
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="capital">Initial Capital (₹)</Label>
                <Input 
                  id="capital" 
                  type="number"
                  min={1000}
                  value={formData.initial_capital}
                  onChange={(e) => setFormData(prev => ({ ...prev, initial_capital: parseFloat(e.target.value) }))}
                  required
                />
              </div>

              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="source">Data Source</Label>
                <Select
                  value={formData.source}
                  onValueChange={(val) => setFormData(prev => ({ ...prev, source: val }))}
                >
                  <SelectTrigger id="source">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="db">Local Historify Database (Fastest)</SelectItem>
                    <SelectItem value="api">Broker API (Live / Recent data)</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground mt-1">Select where to fetch historical data from.</p>
              </div>
            </div>

            <Button type="submit" className="w-full" disabled={isLoading}>
              {isLoading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Running Backtest & Optimizer...
                </>
              ) : (
                'Run Backtest'
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
