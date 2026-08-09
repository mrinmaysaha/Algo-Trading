import { useEffect, useState, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router'
import {
  ArrowLeft,
  ExternalLink,
  Lightbulb,
  Activity,
  FileSpreadsheet,
  TrendingUp,
  TrendingDown,
  ShieldAlert,
  BarChart3,
  Search,
  PieChart,
  Clock
} from 'lucide-react'
import {
  createChart,
  ColorType,
  CandlestickSeries,
  HistogramSeries,
  createSeriesMarkers,
  type Time
} from 'lightweight-charts'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { usePythonStrategyBacktestStore, type TradeRecord } from '@/stores/pythonStrategyBacktestStore'
import Plot from '@/lib/Plot2D'

// TradingView Chart Component with Dynamic Timeframe Resampling
function TradingViewChart({
  candles = [],
  signals = [],
  symbol = ''
}: {
  candles: { time: string; open: number; high: number; low: number; close: number; volume?: number }[]
  signals: { time: string; type: string; label?: string; price: number }[]
  symbol: string
}) {
  const [activeTf, setActiveTf] = useState<'original' | '1m' | '3m' | '5m' | '15m' | '1h' | '1d'>('original')
  const chartContainerRef = useRef<HTMLDivElement>(null)

  // Resample candles according to activeTf
  const displayCandles = useMemo(() => {
    if (activeTf === 'original' || candles.length === 0) return candles

    let intervalMinutes = 5
    if (activeTf === '1m') intervalMinutes = 1
    if (activeTf === '3m') intervalMinutes = 3
    if (activeTf === '5m') intervalMinutes = 5
    if (activeTf === '15m') intervalMinutes = 15
    if (activeTf === '1h') intervalMinutes = 60
    if (activeTf === '1d') intervalMinutes = 1440

    const grouped: Record<string, { time: string; open: number; high: number; low: number; close: number; volume: number }> = {}

    for (const c of candles) {
      const dt = new Date(c.time.replace(' ', 'T'))
      if (isNaN(dt.getTime())) continue

      let key = ''
      if (activeTf === '1d') {
        key = `${c.time.slice(0, 10)} 09:15`
      } else {
        const totalMinutes = dt.getHours() * 60 + dt.getMinutes()
        const roundedMinutes = Math.floor(totalMinutes / intervalMinutes) * intervalMinutes
        const hrs = Math.floor(roundedMinutes / 60).toString().padStart(2, '0')
        const mins = (roundedMinutes % 60).toString().padStart(2, '0')
        key = `${c.time.slice(0, 10)} ${hrs}:${mins}`
      }

      if (!grouped[key]) {
        grouped[key] = {
          time: key,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
          volume: c.volume || 0
        }
      } else {
        grouped[key].high = Math.max(grouped[key].high, c.high)
        grouped[key].low = Math.min(grouped[key].low, c.low)
        grouped[key].close = c.close
        grouped[key].volume += (c.volume || 0)
      }
    }

    return Object.values(grouped).sort((a, b) => a.time.localeCompare(b.time))
  }, [candles, activeTf])

  useEffect(() => {
    if (!chartContainerRef.current || displayCandles.length === 0) return

    const container = chartContainerRef.current
    container.innerHTML = '' // clear previous chart

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 480,
      layout: {
        background: { type: ColorType.Solid, color: '#131722' },
        textColor: '#d1d4dc',
      },
      grid: {
        vertLines: { color: 'rgba(42, 46, 57, 0.6)' },
        horzLines: { color: 'rgba(42, 46, 57, 0.6)' },
      },
      rightPriceScale: {
        borderColor: 'rgba(42, 46, 57, 0.8)',
      },
      timeScale: {
        borderColor: 'rgba(42, 46, 57, 0.8)',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        vertLine: { color: '#758696', labelBackgroundColor: '#2a2e39' },
        horzLine: { color: '#758696', labelBackgroundColor: '#2a2e39' },
      },
    })

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    })

    // Parse time strings ("YYYY-MM-DD HH:MM") to unix seconds with IST timezone offset
    const formattedCandles = displayCandles
      .map(c => {
        const isoStr = c.time.includes('T') ? c.time : `${c.time.replace(' ', 'T')}:00+05:30`
        const dateObj = new Date(isoStr)
        const timestamp = Math.floor(dateObj.getTime() / 1000)
        return {
          time: (isNaN(timestamp) ? c.time : timestamp) as Time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
          volume: c.volume || 0
        }
      })
      .sort((a, b) => (a.time as number) - (b.time as number))

    // Remove duplicates by time
    const uniqueCandles = formattedCandles.filter(
      (item, index, self) => index === self.findIndex(t => t.time === item.time)
    )

    candlestickSeries.setData(uniqueCandles)

    // Volume histogram series
    if (displayCandles.some(c => (c.volume || 0) > 0)) {
      const volumeSeries = chart.addSeries(HistogramSeries, {
        color: '#26a69a',
        priceFormat: { type: 'volume' },
        priceScaleId: '',
      })
      volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      })
      const volumeData = uniqueCandles.map(c => {
        const isUp = c.close >= c.open
        return {
          time: c.time,
          value: c.volume,
          color: isUp ? 'rgba(38, 166, 154, 0.4)' : 'rgba(239, 83, 80, 0.4)',
        }
      })
      volumeSeries.setData(volumeData)
    }

    // Set TradingView Markers for Buy / Sell / CE / PE
    const candleTimes = new Set(uniqueCandles.map(c => c.time))
    
    const markers = signals
      .map(s => {
        const isoStr = s.time.includes('T') ? s.time : `${s.time.replace(' ', 'T')}:00+05:30`
        const dateObj = new Date(isoStr)
        const timestamp = Math.floor(dateObj.getTime() / 1000)
        let t = (isNaN(timestamp) ? s.time : timestamp) as Time

        if (!candleTimes.has(t)) {
          const tNum = typeof t === 'number' ? t : 0
          if (tNum > 0 && uniqueCandles.length > 0) {
            let minDiff = Infinity
            let bestCandleTime: Time | null = null
            for (const cand of uniqueCandles) {
              const candTimeNum = typeof cand.time === 'number' ? cand.time : 0
              const diff = Math.abs(candTimeNum - tNum)
              if (diff < minDiff && diff <= 86400) {
                minDiff = diff
                bestCandleTime = cand.time
              }
            }
            if (bestCandleTime !== null) {
              t = bestCandleTime
            } else {
              return null
            }
          } else {
            return null
          }
        }

        const isBuy = s.type.includes('buy') && !s.type.includes('pe')
        const isPe = s.type.includes('pe') || s.label?.includes('PE')
        const labelText = s.label || (isPe ? 'BUY PE' : isBuy ? 'BUY CE' : 'SELL')

        return {
          time: t,
          position: isPe ? 'aboveBar' : 'belowBar',
          color: isPe ? '#ef5350' : '#26a69a',
          shape: isPe ? 'arrowDown' : 'arrowUp',
          text: labelText,
        }
      })
      .filter(Boolean)
      .sort((a, b) => (a!.time as number) - (b!.time as number))

    createSeriesMarkers(candlestickSeries, markers as any)

    chart.timeScale().fitContent()

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth })
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [displayCandles, signals, symbol])

  return (
    <div className="w-full relative rounded-lg overflow-hidden border border-border/50 bg-[#131722] p-1 space-y-1">
      {/* Top Controls Bar with Timeframe Switcher */}
      <div className="flex flex-wrap items-center justify-between px-3 py-2 border-b border-[#2a2e39] text-xs font-semibold text-[#d1d4dc] gap-2">
        <div className="flex items-center gap-3">
          <span className="font-bold text-white">{symbol}</span>
          <span className="text-xs text-muted-foreground font-normal">TradingView Interactive Engine (09:15-15:30 IST)</span>
        </div>

        {/* Timeframe Switcher */}
        <div className="flex items-center gap-1 bg-[#1e222d] p-0.5 rounded border border-[#2a2e39]">
          <span className="text-[10px] text-muted-foreground px-1.5 flex items-center gap-1">
            <Clock className="h-3 w-3" /> TF:
          </span>
          {(['original', '1m', '3m', '5m', '15m', '1h', '1d'] as const).map(tf => (
            <button
              key={tf}
              onClick={() => setActiveTf(tf)}
              className={`px-2 py-0.5 text-xs font-medium rounded transition-colors ${
                activeTf === tf
                  ? 'bg-[#2962ff] text-white font-bold'
                  : 'text-[#848e9c] hover:text-white hover:bg-[#2a2e39]'
              }`}
            >
              {tf === 'original' ? 'Auto' : tf.toUpperCase()}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-[#26a69a]"></span> BUY CE (Call / Bullish)
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-[#ef5350]"></span> BUY PE (Put / Bearish)
          </span>
        </div>
      </div>

      <div ref={chartContainerRef} className="w-full h-[480px]" />
    </div>
  )
}

export default function PythonStrategyBacktesterResults() {
  const navigate = useNavigate()
  const { result } = usePythonStrategyBacktestStore()

  const [selectedSymbol, setSelectedSymbol] = useState<string>('')
  const [tradeSearch, setTradeSearch] = useState<string>('')
  const [tradeFilter, setTradeFilter] = useState<'all' | 'winning' | 'losing'>('all')

  useEffect(() => {
    if (!result) {
      navigate('/tools/python-backtester')
    } else if (result.symbols && result.symbols.length > 0) {
      setSelectedSymbol(result.symbols[0])
    } else if (result.symbol) {
      setSelectedSymbol(result.symbol)
    }
  }, [result, navigate])

  if (!result) return null

  const {
    metrics,
    optimization,
    tearsheet_url,
    parameters,
    symbol,
    symbols = [],
    equity_curve = [],
    drawdown_curve = [],
    trades = [],
    price_charts = {},
    portfolio_breakdown = [],
    heatmap_html = '',
    assumptions = {},
    manifest = {}
  } = result

  // Filtered trade list
  const filteredTrades = useMemo(() => {
    return trades.filter((t: TradeRecord) => {
      const matchesSearch =
        t.symbol.toLowerCase().includes(tradeSearch.toLowerCase()) ||
        (t.date && t.date.includes(tradeSearch)) ||
        (t.day && t.day.toLowerCase().includes(tradeSearch.toLowerCase())) ||
        t.entry_time.includes(tradeSearch) ||
        t.exit_time.includes(tradeSearch) ||
        (t.action && t.action.toLowerCase().includes(tradeSearch.toLowerCase())) ||
        (t.option_type && t.option_type.toLowerCase().includes(tradeSearch.toLowerCase()))
      
      if (!matchesSearch) return false
      if (tradeFilter === 'winning') return t.result === 'WIN' || t.pnl > 0
      if (tradeFilter === 'losing') return t.result === 'LOSS' || t.pnl <= 0
      return true
    })
  }, [trades, tradeSearch, tradeFilter])

  // Extract price chart for active selected symbol
  const activeChartData = price_charts[selectedSymbol] || { candles: [], signals: [] }

  // Plotly Data for Equity Curve
  const equityPlotData = [
    {
      x: equity_curve.map(e => e.date),
      y: equity_curve.map(e => e.value),
      type: 'scatter',
      mode: 'lines',
      name: 'Portfolio Equity',
      line: { color: '#10b981', width: 2 },
      fill: 'tozeroy',
      fillcolor: 'rgba(16, 185, 129, 0.08)',
    }
  ]

  // Plotly Data for Drawdown Curve
  const drawdownPlotData = [
    {
      x: drawdown_curve.map(d => d.date),
      y: drawdown_curve.map(d => d.drawdown),
      type: 'scatter',
      mode: 'lines',
      name: 'Drawdown %',
      line: { color: '#ef4444', width: 1.5 },
      fill: 'tozeroy',
      fillcolor: 'rgba(239, 68, 68, 0.15)',
    }
  ]

  const totalReturn = metrics['Total Return [%]'] ?? 0
  const isPositiveReturn = Number(totalReturn) >= 0

  return (
    <div className="container py-8 max-w-6xl mx-auto space-y-8">
      {/* Header & Main Action Button */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 border-b pb-6">
        <div className="flex items-center gap-4">
          <Button variant="outline" size="icon" onClick={() => navigate('/tools/python-backtester')}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-3xl font-bold tracking-tight">Portfolio Report: {symbol}</h1>
              {symbols.length > 1 && (
                <span className="bg-primary/10 text-primary text-xs font-semibold px-2.5 py-0.5 rounded-full">
                  Portfolio ({symbols.length} Assets)
                </span>
              )}
            </div>
            <p className="text-muted-foreground text-sm mt-1">
              VectorBT Simulation & TradingView Analytics Dashboard (IST Market Hours 09:15 - 15:30)
            </p>
          </div>
        </div>

        {tearsheet_url && (
          <Button
            size="lg"
            onClick={() => window.open(tearsheet_url, '_blank')}
            className="bg-emerald-600 hover:bg-emerald-700 text-white shadow-md gap-2"
          >
            <FileSpreadsheet className="h-5 w-5" />
            Open OpenStatz Tearsheet
            <ExternalLink className="h-4 w-4 ml-1 opacity-80" />
          </Button>
        )}
      </div>

      {/* KPI Cards Scorecard Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
        <Card className="bg-card">
          <CardContent className="p-4 space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Total Return</p>
            <p className={`text-2xl font-bold flex items-center gap-1 ${isPositiveReturn ? 'text-green-500' : 'text-red-500'}`}>
              {isPositiveReturn ? <TrendingUp className="h-4 w-4 inline" /> : <TrendingDown className="h-4 w-4 inline" />}
              {Number(totalReturn).toFixed(2)}%
            </p>
          </CardContent>
        </Card>

        <Card className="bg-card">
          <CardContent className="p-4 space-y-1">
            <p className="text-xs font-medium text-muted-foreground">CAGR</p>
            <p className="text-2xl font-bold text-foreground">
              {metrics['CAGR [%]'] ? `${Number(metrics['CAGR [%]']).toFixed(2)}%` : 'N/A'}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-card">
          <CardContent className="p-4 space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Sharpe Ratio</p>
            <p className="text-2xl font-bold text-foreground">
              {metrics['Sharpe Ratio'] ? Number(metrics['Sharpe Ratio']).toFixed(2) : 'N/A'}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-card">
          <CardContent className="p-4 space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Sortino Ratio</p>
            <p className="text-2xl font-bold text-foreground">
              {metrics['Sortino Ratio'] ? Number(metrics['Sortino Ratio']).toFixed(2) : 'N/A'}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-card">
          <CardContent className="p-4 space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Max Drawdown</p>
            <p className="text-2xl font-bold text-red-500">
              {metrics['Max Drawdown [%]'] ? `${Number(metrics['Max Drawdown [%]']).toFixed(2)}%` : 'N/A'}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-card">
          <CardContent className="p-4 space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Win Rate</p>
            <p className="text-2xl font-bold text-foreground">
              {metrics['Win Rate [%]'] ? `${Number(metrics['Win Rate [%]']).toFixed(2)}%` : 'N/A'}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Tabs for Charts & In-depth Analytics */}
      <Tabs defaultValue="price" className="space-y-6">
        <TabsList className="grid w-full grid-cols-3 max-w-md">
          <TabsTrigger value="price" className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4" /> TradingView Price Chart
          </TabsTrigger>
          <TabsTrigger value="equity" className="flex items-center gap-2">
            <Activity className="h-4 w-4" /> Equity & DD
          </TabsTrigger>
          {symbols.length > 1 && (
            <TabsTrigger value="breakdown" className="flex items-center gap-2">
              <PieChart className="h-4 w-4" /> Portfolio Breakdown
            </TabsTrigger>
          )}
        </TabsList>

        {/* Tab 1: TradingView Price Chart with Buy (CE) / Sell (PE) Markers */}
        <TabsContent value="price" className="space-y-6">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-4">
              <div>
                <CardTitle className="text-lg font-semibold">TradingView Price & Trade Execution Chart</CardTitle>
                <CardDescription>Overlaid BUY CE (Call) & BUY PE (Put) markers with timeframe switcher</CardDescription>
              </div>
              {symbols.length > 1 && (
                <div className="w-48">
                  <Select value={selectedSymbol} onValueChange={setSelectedSymbol}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select Asset" />
                    </SelectTrigger>
                    <SelectContent>
                      {symbols.map(s => (
                        <SelectItem key={s} value={s}>{s}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </CardHeader>
            <CardContent>
              {activeChartData.candles.length > 0 ? (
                <TradingViewChart
                  candles={activeChartData.candles}
                  signals={activeChartData.signals}
                  symbol={selectedSymbol}
                />
              ) : (
                <div className="py-16 text-center text-muted-foreground">
                  Select a symbol to view price & signal data.
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 2: Equity Curve & Drawdown */}
        <TabsContent value="equity" className="space-y-6">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-lg font-semibold flex items-center justify-between">
                <span>Portfolio Equity Growth</span>
                <span className="text-sm font-normal text-muted-foreground">
                  Initial Capital: ₹{metrics['Initial Capital']?.toLocaleString() || '100,000'} | Final: ₹{metrics['Final Portfolio Value']?.toLocaleString() || 'N/A'}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {equity_curve.length > 0 ? (
                <div className="w-full overflow-hidden">
                  <Plot
                    data={equityPlotData}
                    layout={{
                      autosize: true,
                      height: 320,
                      margin: { l: 50, r: 20, t: 20, b: 40 },
                      paper_bgcolor: 'transparent',
                      plot_bgcolor: 'transparent',
                      xaxis: { showgrid: false, tickfont: { color: '#888' } },
                      yaxis: { showgrid: true, gridcolor: 'rgba(255,255,255,0.08)', tickfont: { color: '#888' } },
                      showlegend: false
                    }}
                    useResizeHandler={true}
                    style={{ width: '100%' }}
                  />
                </div>
              ) : (
                <div className="py-12 text-center text-muted-foreground">No equity curve data available.</div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-lg font-semibold">Underwater Drawdown (%)</CardTitle>
            </CardHeader>
            <CardContent>
              {drawdown_curve.length > 0 ? (
                <div className="w-full overflow-hidden">
                  <Plot
                    data={drawdownPlotData}
                    layout={{
                      autosize: true,
                      height: 200,
                      margin: { l: 50, r: 20, t: 10, b: 40 },
                      paper_bgcolor: 'transparent',
                      plot_bgcolor: 'transparent',
                      xaxis: { showgrid: false, tickfont: { color: '#888' } },
                      yaxis: { showgrid: true, gridcolor: 'rgba(255,255,255,0.08)', tickfont: { color: '#888' }, suffix: '%' },
                      showlegend: false
                    }}
                    useResizeHandler={true}
                    style={{ width: '100%' }}
                  />
                </div>
              ) : (
                <div className="py-8 text-center text-muted-foreground">No drawdown data available.</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 3: Multi-Asset Breakdown */}
        {symbols.length > 1 && (
          <TabsContent value="breakdown" className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg font-semibold">Multi-Asset Portfolio Summary</CardTitle>
                <CardDescription>Individual asset return and risk contribution</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="relative w-full overflow-auto">
                  <table className="w-full text-sm text-left">
                    <thead className="text-xs uppercase bg-muted text-muted-foreground border-b">
                      <tr>
                        <th className="py-3 px-4">Symbol</th>
                        <th className="py-3 px-4 text-right">Trades Count</th>
                        <th className="py-3 px-4 text-right">Net PnL (₹)</th>
                        <th className="py-3 px-4 text-right">Total Return (%)</th>
                        <th className="py-3 px-4 text-right">Max Drawdown (%)</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {portfolio_breakdown.map((item) => (
                        <tr key={item.symbol} className="hover:bg-muted/50">
                          <td className="py-3 px-4 font-semibold">{item.symbol}</td>
                          <td className="py-3 px-4 text-right">{item.trades}</td>
                          <td className={`py-3 px-4 text-right font-medium ${item.pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                            ₹{item.pnl.toLocaleString()}
                          </td>
                          <td className={`py-3 px-4 text-right font-medium ${item.total_return_pct >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                            {item.total_return_pct}%
                          </td>
                          <td className="py-3 px-4 text-right text-red-500">{item.max_drawdown_pct}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        )}
      </Tabs>

      {/* Extended Risk Statistics & AI Optimization Grid */}
      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>In-Depth Performance Statistics</CardTitle>
            <CardDescription>Risk-adjusted metrics & trade statistics</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">Calmar Ratio</p>
                <p className="text-lg font-semibold">{metrics['Calmar Ratio'] ?? 'N/A'}</p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">Profit Factor</p>
                <p className="text-lg font-semibold">{metrics['Profit Factor'] ?? 'N/A'}</p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">Trade Expectancy</p>
                <p className="text-lg font-semibold">₹{metrics['Expectancy'] ? Number(metrics['Expectancy']).toFixed(2) : 'N/A'}</p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">Total Trades</p>
                <p className="text-lg font-semibold">{metrics['Total Trades'] ?? 0}</p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">Gross Profit</p>
                <p className="text-lg font-semibold text-green-500">₹{metrics['Gross Profit']?.toLocaleString() ?? 0}</p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">Gross Loss</p>
                <p className="text-lg font-semibold text-red-500">₹{metrics['Gross Loss']?.toLocaleString() ?? 0}</p>
              </div>
            </div>

            <div className="pt-4 border-t">
              <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">Extracted Strategy Parameters</h4>
              <div className="flex flex-wrap gap-2">
                {Object.entries(parameters).map(([key, val]) => (
                  <span key={key} className="inline-flex items-center rounded-md bg-muted px-2.5 py-1 text-xs font-medium ring-1 ring-inset ring-muted-foreground/20">
                    {key}: <span className="font-bold ml-1">{String(val)}</span>
                  </span>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card className="border-primary/40 bg-primary/5">
            <CardHeader>
              <div className="flex items-center gap-2">
                <Lightbulb className="h-5 w-5 text-yellow-500" />
                <CardTitle>AI Optimization Suggestions</CardTitle>
              </div>
              <CardDescription>VectorBT grid-search strategy optimization</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {optimization && optimization.parameter ? (
                <>
                  <div className="flex justify-between border-b pb-2 text-sm">
                    <span className="text-muted-foreground">Optimized Target</span>
                    <span className="font-medium">{optimization.parameter}</span>
                  </div>
                  <div className="flex justify-between border-b pb-2 text-sm">
                    <span className="text-muted-foreground">Tested Ranges</span>
                    <span className="font-medium text-right">{optimization.tested_ranges}</span>
                  </div>
                  <div className="flex justify-between border-b pb-2 text-sm">
                    <span className="text-muted-foreground">Best Parameter Found</span>
                    <span className="font-bold text-primary">{optimization.best_found}</span>
                  </div>
                  <div className="flex justify-between pt-1 text-sm">
                    <span className="text-muted-foreground">Potential Gain</span>
                    <span className="font-bold text-green-500">{optimization.improvement}</span>
                  </div>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">No parameter optimization suggestions for this script.</p>
              )}
            </CardContent>
          </Card>

          {/* OpenStatz Tearsheet Callout Banner */}
          {tearsheet_url ? (
            <Alert className="bg-emerald-500/10 border-emerald-500/30">
              <FileSpreadsheet className="h-5 w-5 text-emerald-500" />
              <AlertTitle className="text-emerald-600 font-semibold">Interactive OpenStatz Dashboard Ready</AlertTitle>
              <AlertDescription className="mt-2 space-y-3">
                <p className="text-sm text-muted-foreground">
                  OpenStatz generated a complete standalone HTML report with drawdowns, return distribution, and trade analytics.
                </p>
                <Button 
                  onClick={() => window.open(tearsheet_url, '_blank')}
                  className="bg-emerald-600 hover:bg-emerald-700 text-white w-full sm:w-auto"
                >
                  <ExternalLink className="mr-2 h-4 w-4" />
                  View OpenStatz Tearsheet
                </Button>
              </AlertDescription>
            </Alert>
          ) : (
            <Alert variant="destructive">
              <ShieldAlert className="h-5 w-5" />
              <AlertTitle>Tearsheet Generation</AlertTitle>
              <AlertDescription>
                OpenStatz package is not installed or failed to render the tearsheet dashboard.
              </AlertDescription>
            </Alert>
          )}
        </div>
      </div>

      {/* StockMock / AlgoTest Monthly PnL Heatmap Matrix */}
      {heatmap_html && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg font-semibold">StockMock Monthly PnL Matrix (₹)</CardTitle>
            <CardDescription>Monthly profit distribution & performance heatmap</CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <div dangerouslySetInnerHTML={{ __html: heatmap_html }} />
          </CardContent>
        </Card>
      )}

      {/* Reproducibility Manifest & Transparency Disclosures */}
      {(manifest?.run_id || assumptions?.pricing_model) && (
        <div className="grid gap-6 md:grid-cols-2">
          {manifest?.run_id && (
            <Card className="bg-card">
              <CardHeader>
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <ShieldAlert className="h-4 w-4 text-primary" /> Audit Trail & Reproducibility Manifest
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs font-mono">
                <div className="flex justify-between border-b pb-1">
                  <span className="text-muted-foreground">Run ID</span>
                  <span className="font-bold text-foreground">{manifest.run_id}</span>
                </div>
                <div className="flex justify-between border-b pb-1">
                  <span className="text-muted-foreground">Code Hash</span>
                  <span>{manifest.strategy_code_hash}</span>
                </div>
                <div className="flex justify-between border-b pb-1">
                  <span className="text-muted-foreground">Engine Version</span>
                  <span className="text-emerald-500 font-bold">{manifest.engine_version}</span>
                </div>
                <div className="flex justify-between pt-1">
                  <span className="text-muted-foreground">Generated At</span>
                  <span>{manifest.generated_at?.slice(0, 19).replace('T', ' ')}</span>
                </div>
              </CardContent>
            </Card>
          )}

          {assumptions?.pricing_model && (
            <Card className="bg-card">
              <CardHeader>
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <Lightbulb className="h-4 w-4 text-yellow-500" /> Backtest Methodology & Pricing Model
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs">
                <div className="flex justify-between border-b pb-1">
                  <span className="text-muted-foreground">Pricing Model</span>
                  <span className="font-semibold">{assumptions.pricing_model}</span>
                </div>
                <div className="flex justify-between border-b pb-1">
                  <span className="text-muted-foreground">DTE Calculation</span>
                  <span>{assumptions.dte_calculation}</span>
                </div>
                <div className="flex justify-between border-b pb-1">
                  <span className="text-muted-foreground">Slippage Model</span>
                  <span>{assumptions.slippage_model}</span>
                </div>
                <div className="flex justify-between pt-1">
                  <span className="text-muted-foreground">Confidence Grade</span>
                  <span className="font-bold text-emerald-400">{assumptions.confidence_grade}</span>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Interactive Trades Log Table matching requested format */}
      <Card>
        <CardHeader className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <CardTitle>Trade Execution History ({trades.length} Trades)</CardTitle>
            <CardDescription>Structured log of filled signals (Market Hours 09:15 - 15:30 IST)</CardDescription>
          </div>

          <div className="flex flex-col sm:flex-row items-center gap-2">
            <div className="relative w-full sm:w-64">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search CE, PE, date..."
                className="pl-8"
                value={tradeSearch}
                onChange={e => setTradeSearch(e.target.value)}
              />
            </div>
            <div className="flex items-center gap-1 bg-muted p-1 rounded-md">
              <Button
                variant={tradeFilter === 'all' ? 'default' : 'ghost'}
                size="sm"
                onClick={() => setTradeFilter('all')}
              >
                All
              </Button>
              <Button
                variant={tradeFilter === 'winning' ? 'default' : 'ghost'}
                size="sm"
                onClick={() => setTradeFilter('winning')}
              >
                Winners
              </Button>
              <Button
                variant={tradeFilter === 'losing' ? 'default' : 'ghost'}
                size="sm"
                onClick={() => setTradeFilter('losing')}
              >
                Losers
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="relative w-full overflow-auto max-h-[500px]">
            <table className="w-full text-sm text-left">
              <thead className="text-xs uppercase bg-muted text-muted-foreground sticky top-0">
                <tr>
                  <th className="py-3 px-3">#</th>
                  <th className="py-3 px-3">Date</th>
                  <th className="py-3 px-3">Day</th>
                  <th className="py-3 px-3">Symbol</th>
                  <th className="py-3 px-3">Entry Time</th>
                  <th className="py-3 px-3">Exit Time</th>
                  <th className="py-3 px-3 text-center">Dir</th>
                  <th className="py-3 px-3 text-right">Spot Entry</th>
                  <th className="py-3 px-3 text-right">Spot Exit</th>
                  <th className="py-3 px-3 text-center">Result</th>
                  <th className="py-3 px-3 text-right">PnL (Pts)</th>
                  <th className="py-3 px-3 text-right">PnL (₹)</th>
                  <th className="py-3 px-3 text-right">Holding Time</th>
                </tr>
              </thead>
              <tbody className="divide-y font-mono">
                {filteredTrades.length > 0 ? (
                  filteredTrades.map(t => {
                    const isWin = t.result === 'WIN' || t.pnl > 0
                    const isCe = t.option_type === 'CE' || t.direction?.includes('CE')
                    const isPe = t.option_type === 'PE' || t.direction?.includes('PE')
                    const ptsVal = t.pnl_pts ?? (t.entry_price > 0 ? (isCe ? t.exit_price - t.entry_price : t.entry_price - t.exit_price) : 0)

                    return (
                      <tr key={`${t.symbol}-${t.trade_id}`} className="hover:bg-muted/40 text-xs sm:text-sm">
                        <td className="py-2.5 px-3 text-muted-foreground">{t.trade_id}</td>
                        <td className="py-2.5 px-3 font-sans font-medium whitespace-nowrap">{t.date || t.entry_time.slice(0, 10)}</td>
                        <td className="py-2.5 px-3 font-sans text-muted-foreground">{t.day || '—'}</td>
                        <td className="py-2.5 px-3 font-sans font-semibold">{t.symbol}</td>
                        <td className="py-2.5 px-3 whitespace-nowrap text-xs text-muted-foreground">{t.entry_time}</td>
                        <td className="py-2.5 px-3 whitespace-nowrap text-xs text-muted-foreground">{t.exit_time}</td>
                        <td className="py-2.5 px-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-xs font-bold font-sans ${
                            isCe ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' :
                            isPe ? 'bg-purple-500/20 text-purple-400 border border-purple-500/40' :
                            'bg-blue-500/20 text-blue-400'
                          }`}>
                            {isCe ? 'CE' : isPe ? 'PE' : 'EQ'}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-right">₹{t.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                        <td className="py-2.5 px-3 text-right">₹{t.exit_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                        <td className="py-2.5 px-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-xs font-bold font-sans ${
                            isWin ? 'bg-green-500/20 text-green-400 border border-green-500/30' : 'bg-red-500/20 text-red-400 border border-red-500/30'
                          }`}>
                            {isWin ? 'WIN' : 'LOSS'}
                          </span>
                        </td>
                        <td className={`py-2.5 px-3 text-right font-semibold ${ ptsVal >= 0 ? 'text-green-500' : 'text-red-500' }`}>
                          {ptsVal >= 0 ? '+' : ''}{ptsVal.toFixed(1)} pts
                        </td>
                        <td className={`py-2.5 px-3 text-right font-bold ${ t.pnl >= 0 ? 'text-green-500' : 'text-red-500' }`}>
                          {t.pnl >= 0 ? '+' : ''}₹{t.pnl.toLocaleString()}
                        </td>
                        <td className="py-2.5 px-3 text-right font-sans text-muted-foreground whitespace-nowrap">{t.holding_time || '—'}</td>
                      </tr>
                    )
                  })
                ) : (
                  <tr>
                    <td colSpan={11} className="py-8 text-center text-muted-foreground font-sans">
                      No trades matched your search criteria.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
