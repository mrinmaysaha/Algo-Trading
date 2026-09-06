import {
  Activity,
  BarChart3,
  Bot,
  CheckCircle2,
  ChevronRight,
  Clock,
  Copy,
  Flame,
  MessageCircle,
  Radar,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  TrendingUp,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import {
  type ScannerSignal,
  scannerApi,
} from '@/api/scanner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { makeFormatCurrency } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { showToast } from '@/utils/toast'

export default function Scanner() {
  const { user } = useAuthStore()
  const formatCurrency = useMemo(() => makeFormatCurrency(user?.broker), [user?.broker])

  // State
  const [signals, setSignals] = useState<ScannerSignal[]>([])
  const [isScanning, setIsScanning] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [activeFilter, setActiveFilter] = useState<'ALL' | 'INTRADAY' | 'SWING' | 'OPTIONS'>('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [autoRefresh] = useState(true)

  // Execution modal state
  const [execModalOpen, setExecModalOpen] = useState(false)
  const [selectedSignal, setSelectedSignal] = useState<ScannerSignal | null>(null)
  const [isOptionOrder, setIsOptionOrder] = useState(false)
  const [orderQuantity, setOrderQuantity] = useState<number>(10)
  const [orderProduct, setOrderProduct] = useState<string>('MIS')
  const [isExecuting, setIsExecuting] = useState(false)

  // WhatsApp guide modal state
  const [whatsappModalOpen, setWhatsappModalOpen] = useState(false)

  // Polling ref
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Fetch signals
  const fetchSignals = useCallback(
    async (showLoading = false) => {
      if (showLoading) setIsRefreshing(true)
      try {
        const data = await scannerApi.getSignals(activeFilter)
        if (data.status === 'success' || data.status === 'warning') {
          setSignals(data.signals || [])
          setIsScanning(Boolean(data.is_scanning))
          setLastUpdated(data.last_updated)
        }
      } catch (err: any) {
        console.error('Failed to load scanner signals:', err)
      } finally {
        setIsLoading(false)
        setIsRefreshing(false)
      }
    },
    [activeFilter]
  )

  // Initial load and filter change
  useEffect(() => {
    fetchSignals(true)
  }, [fetchSignals])

  // Polling loop
  useEffect(() => {
    if (!autoRefresh) return
    pollTimerRef.current = setInterval(() => {
      fetchSignals(false)
    }, 6000)

    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    }
  }, [autoRefresh, fetchSignals])

  // Trigger manual universe scan
  const handleTriggerScan = async () => {
    setIsScanning(true)
    try {
      const res = await scannerApi.triggerScan()
      if (res.status === 'success') {
        showToast.success('Universe scan initiated in background. Signals will stream in.')
      } else {
        showToast.info(res.message || 'Scan already running.')
      }
      setTimeout(() => {
        fetchSignals(false)
      }, 1500)
    } catch (err: any) {
      showToast.error('Failed to trigger universe scan: ' + (err.message || 'Unknown error'))
    }
  }

  // Open 1-Click execution modal
  const openOrderModal = (signal: ScannerSignal, isOption: boolean) => {
    setSelectedSignal(signal)
    setIsOptionOrder(isOption)

    if (isOption && signal.option_recommendation) {
      setOrderQuantity(signal.option_recommendation.lot_size || 1)
      setOrderProduct(signal.setup_type === 'INTRADAY' ? 'MIS' : 'NRML')
    } else {
      setOrderQuantity(signal.setup_type === 'INTRADAY' ? 50 : 10)
      setOrderProduct(signal.setup_type === 'INTRADAY' ? 'MIS' : 'CNC')
    }

    setExecModalOpen(true)
  }

  // Confirm and route order
  const handleExecuteOrder = async () => {
    if (!selectedSignal) return
    setIsExecuting(true)

    const isOpt = isOptionOrder && selectedSignal.option_recommendation
    const targetSymbol = isOpt
      ? selectedSignal.option_recommendation!.symbol
      : selectedSignal.symbol
    const exchange = isOpt ? 'NFO' : 'NSE'

    try {
      const res = await scannerApi.execute1Click({
        symbol: targetSymbol,
        exchange,
        quantity: orderQuantity,
        order_type: 'BUY',
        price_type: 'MARKET',
        product: orderProduct,
        signal_data: selectedSignal as unknown as Record<string, unknown>,
      })

      if (res.status === 'success') {
        showToast.success(
          `1-Click order placed for ${targetSymbol} (${orderQuantity} qty)!`,
          'orders'
        )
        setExecModalOpen(false)
      } else {
        showToast.error(res.message || 'Order routing failed', 'orders')
      }
    } catch (err: any) {
      showToast.error(err.response?.data?.message || err.message || 'Execution error', 'orders')
    } finally {
      setIsExecuting(false)
    }
  }

  // Copy WhatsApp command helper
  const copyCommand = (text: string) => {
    navigator.clipboard.writeText(text)
    showToast.success(`Copied "${text}" to clipboard`)
  }

  // Filter signals by search query
  const filteredSignals = useMemo(() => {
    if (!searchQuery.trim()) return signals
    const q = searchQuery.toLowerCase().trim()
    return signals.filter(
      (s) =>
        s.symbol.toLowerCase().includes(q) ||
        s.signal_id.toLowerCase().includes(q) ||
        s.option_recommendation?.symbol.toLowerCase().includes(q)
    )
  }, [signals, searchQuery])

  // Aggregate KPI stats
  const stats = useMemo(() => {
    const total = signals.length
    const intraday = signals.filter((s) => s.setup_type === 'INTRADAY').length
    const swing = signals.filter((s) => s.setup_type === 'SWING').length
    const options = signals.filter((s) => Boolean(s.option_recommendation)).length
    return { total, intraday, swing, options }
  }, [signals])

  return (
    <div className="space-y-6">
      {/* Breadcrumb & Top Bar */}
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground mb-1">
            <Link to="/tools" className="hover:text-foreground transition-colors">
              Tools
            </Link>
            <ChevronRight className="h-3 w-3" />
            <span className="text-foreground">Nifty 500 Scanner & Option Radar</span>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-500">
              <Radar className="h-5 w-5 animate-pulse" />
            </div>
            <div>
              <h1 className="text-xl md:text-2xl font-bold tracking-tight text-foreground flex items-center gap-2">
                Nifty 500 Radar & Two-Way Execution Desk
              </h1>
              <p className="text-xs text-muted-foreground">
                Multi-Timeframe Breakout Scanner • 1-Strike ITM Option Radar • Automated WhatsApp 2-Way Execution
              </p>
            </div>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex flex-wrap items-center gap-2.5">
          {/* Status Badge */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border bg-card/60 backdrop-blur text-xs font-medium">
            <span
              className={`h-2 w-2 rounded-full ${
                isScanning ? 'bg-amber-400 animate-ping' : 'bg-emerald-500'
              }`}
            />
            <span className="text-muted-foreground font-mono">
              {isScanning ? 'Scanning Universe...' : 'Radar Active'}
            </span>
          </div>

          {/* WhatsApp Guide Dialog Trigger */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setWhatsappModalOpen(true)}
            className="text-xs gap-1.5 border-emerald-500/30 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/10"
          >
            <MessageCircle className="h-3.5 w-3.5" />
            <span>WhatsApp Desk</span>
          </Button>

          {/* Refresh Signals */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchSignals(true)}
            disabled={isRefreshing}
            className="text-xs gap-1.5"
            title="Refresh Signal Feed"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </Button>

          {/* Trigger Full Scan Button */}
          <Button
            size="sm"
            onClick={handleTriggerScan}
            disabled={isScanning}
            className="text-xs gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm font-semibold"
          >
            <Sparkles className={`h-3.5 w-3.5 ${isScanning ? 'animate-spin' : ''}`} />
            <span>{isScanning ? 'Scanning...' : 'Scan Universe Now'}</span>
          </Button>
        </div>
      </div>

      {/* KPI Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="bg-card/50 backdrop-blur border-border/60 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Total Active Signals
            </CardTitle>
            <Activity className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-black tracking-tight">{stats.total}</div>
            <p className="text-[11px] text-muted-foreground mt-1 flex items-center gap-1">
              <span>Matching current filters</span>
            </p>
          </CardContent>
        </Card>

        <Card className="bg-card/50 backdrop-blur border-border/60 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              5M Intraday Setups
            </CardTitle>
            <TrendingUp className="h-4 w-4 text-cyan-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-black tracking-tight text-cyan-600 dark:text-cyan-400">
              {stats.intraday}
            </div>
            <p className="text-[11px] text-muted-foreground mt-1">VWAP & Donchian 5M surges</p>
          </CardContent>
        </Card>

        <Card className="bg-card/50 backdrop-blur border-border/60 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              1D Swing Breakouts
            </CardTitle>
            <BarChart3 className="h-4 w-4 text-purple-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-black tracking-tight text-purple-600 dark:text-purple-400">
              {stats.swing}
            </div>
            <p className="text-[11px] text-muted-foreground mt-1">Daily trend breakouts</p>
          </CardContent>
        </Card>

        <Card className="bg-card/50 backdrop-blur border-border/60 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              F&O Options Radar
            </CardTitle>
            <Zap className="h-4 w-4 text-amber-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-black tracking-tight text-amber-600 dark:text-amber-400">
              {stats.options}
            </div>
            <p className="text-[11px] text-muted-foreground mt-1">1-Strike ITM options mapped</p>
          </CardContent>
        </Card>
      </div>

      {/* Filter and Search Navigation Bar */}
      <Card className="border-border/60 bg-card/60 backdrop-blur shadow-sm">
        <CardContent className="p-3">
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
            {/* Filter Tabs */}
            <Tabs
              value={activeFilter}
              onValueChange={(v) => setActiveFilter(v as any)}
              className="w-full sm:w-auto"
            >
              <TabsList className="grid grid-cols-4 sm:flex w-full h-9 bg-muted/70">
                <TabsTrigger value="ALL" className="text-xs">
                  All Signals
                </TabsTrigger>
                <TabsTrigger value="INTRADAY" className="text-xs">
                  Intraday (5M)
                </TabsTrigger>
                <TabsTrigger value="SWING" className="text-xs">
                  Swing (1D)
                </TabsTrigger>
                <TabsTrigger value="OPTIONS" className="text-xs">
                  F&O Options
                </TabsTrigger>
              </TabsList>
            </Tabs>

            {/* Search and Last Updated */}
            <div className="flex items-center gap-3">
              <div className="relative w-full sm:w-60">
                <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
                <Input
                  type="text"
                  placeholder="Filter stock (e.g. TATA, REL)..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="h-9 pl-8 text-xs bg-background/80"
                />
              </div>

              {lastUpdated && (
                <span className="hidden lg:flex items-center gap-1.5 text-[11px] text-muted-foreground whitespace-nowrap font-mono">
                  <Clock className="h-3 w-3" />
                  <span>{lastUpdated}</span>
                </span>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Main Signals Table Card */}
      <Card className="border-border/60 bg-card shadow-md overflow-hidden">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader className="bg-muted/40">
              <TableRow className="text-[11px] uppercase tracking-wider font-bold">
                <TableHead className="w-16">ID</TableHead>
                <TableHead>Stock / Symbol</TableHead>
                <TableHead>Setup Type</TableHead>
                <TableHead>Spot Entry</TableHead>
                <TableHead>Stop Loss</TableHead>
                <TableHead>Target 1 (1:1.5)</TableHead>
                <TableHead>Target 2 (1:3.0)</TableHead>
                <TableHead>Momentum / Surge</TableHead>
                <TableHead className="min-w-[200px]">Option Recommendation</TableHead>
                <TableHead className="text-right pr-4">1-Click Execution</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={10} className="h-44 text-center">
                    <div className="flex flex-col items-center justify-center space-y-2 text-muted-foreground">
                      <Radar className="h-8 w-8 animate-spin text-emerald-500" />
                      <p className="text-xs">Scanning universe & computing technical signals...</p>
                    </div>
                  </TableCell>
                </TableRow>
              ) : filteredSignals.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={10} className="h-44 text-center">
                    <div className="flex flex-col items-center justify-center space-y-3 py-6">
                      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted/60 text-muted-foreground">
                        <Radar className="h-6 w-6" />
                      </div>
                      <div className="space-y-1">
                        <h4 className="text-sm font-semibold text-foreground">
                          No Breakout Signals Found
                        </h4>
                        <p className="text-xs text-muted-foreground max-w-sm">
                          {searchQuery
                            ? `No stocks matching "${searchQuery}". Try clearing search.`
                            : 'No high-conviction breakout setups meeting ATR & volume surge criteria currently. Click below to run a fresh scan.'}
                        </p>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={handleTriggerScan}
                        disabled={isScanning}
                        className="text-xs gap-1.5 border-emerald-500/30 text-emerald-600 dark:text-emerald-400"
                      >
                        <Sparkles className="h-3.5 w-3.5" />
                        <span>Run Full Universe Scan</span>
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                filteredSignals.map((sig) => {
                  const isIntra = sig.setup_type === 'INTRADAY'
                  const opt = sig.option_recommendation

                  return (
                    <TableRow
                      key={sig.signal_id}
                      className="hover:bg-muted/40 transition-colors font-mono text-xs"
                    >
                      {/* Signal ID */}
                      <TableCell className="font-bold text-muted-foreground">
                        <span className="px-1.5 py-0.5 rounded bg-muted text-[11px]">
                          #{sig.signal_id}
                        </span>
                      </TableCell>

                      {/* Stock Symbol */}
                      <TableCell className="font-sans">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-sm text-foreground tracking-tight">
                            {sig.symbol}
                          </span>
                          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                            NSE
                          </Badge>
                        </div>
                        <span className="text-[10px] text-muted-foreground font-mono">
                          {sig.timestamp || 'Live'}
                        </span>
                      </TableCell>

                      {/* Setup Type */}
                      <TableCell className="font-sans">
                        {isIntra ? (
                          <Badge className="bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/20 text-[10px] font-bold">
                            5M INTRADAY
                          </Badge>
                        ) : (
                          <Badge className="bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/30 hover:bg-purple-500/20 text-[10px] font-bold">
                            1D SWING
                          </Badge>
                        )}
                      </TableCell>

                      {/* Spot Price */}
                      <TableCell className="font-bold text-emerald-600 dark:text-emerald-400">
                        {formatCurrency(sig.spot_price)}
                      </TableCell>

                      {/* Stop Loss */}
                      <TableCell className="text-rose-500 dark:text-rose-400 font-medium">
                        {formatCurrency(sig.sl)}
                      </TableCell>

                      {/* Target 1 */}
                      <TableCell className="text-cyan-600 dark:text-cyan-400 font-medium">
                        {formatCurrency(sig.tp1)}
                      </TableCell>

                      {/* Target 2 */}
                      <TableCell className="text-emerald-600 dark:text-emerald-400 font-medium">
                        {formatCurrency(sig.tp2)}
                      </TableCell>

                      {/* Momentum / Surge */}
                      <TableCell className="font-sans">
                        <div className="flex flex-col gap-1">
                          <div className="flex items-center gap-1.5 text-[11px]">
                            <span className="text-muted-foreground font-medium">RSI:</span>
                            <span
                              className={`font-mono font-bold ${
                                sig.rsi > 70
                                  ? 'text-amber-500'
                                  : sig.rsi > 55
                                  ? 'text-emerald-500'
                                  : 'text-muted-foreground'
                              }`}
                            >
                              {sig.rsi}
                            </span>
                          </div>
                          <div className="flex items-center gap-1 text-[11px]">
                            <Flame className="h-3 w-3 text-orange-500" />
                            <span className="font-mono font-semibold text-orange-500">
                              {sig.volume_surge}x Vol
                            </span>
                          </div>
                        </div>
                      </TableCell>

                      {/* Option Recommendation */}
                      <TableCell className="font-sans">
                        {opt ? (
                          <div className="flex flex-col gap-0.5 p-2 rounded-lg bg-amber-500/5 border border-amber-500/20">
                            <div className="flex items-center justify-between">
                              <span className="font-mono font-bold text-xs text-amber-600 dark:text-amber-400">
                                {opt.symbol}
                              </span>
                              <Badge className="bg-amber-500/20 text-amber-600 dark:text-amber-400 text-[9px] px-1 py-0 border-0">
                                {opt.strike_type}
                              </Badge>
                            </div>
                            <div className="text-[11px] text-muted-foreground font-mono mt-0.5">
                              Lot: {opt.lot_size} | Est: ₹{opt.opt_entry.toFixed(2)}
                            </div>
                            <div className="text-[10px] text-muted-foreground font-mono flex items-center gap-2">
                              <span className="text-rose-500">SL: ₹{opt.opt_sl.toFixed(2)}</span>
                              <span className="text-emerald-500">
                                TP: ₹{opt.opt_tp1.toFixed(2)}
                              </span>
                            </div>
                          </div>
                        ) : (
                          <span className="text-muted-foreground text-[11px] italic">
                            Cash Equity Only
                          </span>
                        )}
                      </TableCell>

                      {/* 1-Click Action Buttons */}
                      <TableCell className="text-right pr-4 font-sans">
                        <div className="flex items-center justify-end gap-1.5">
                          {opt && (
                            <Button
                              size="sm"
                              onClick={() => openOrderModal(sig, true)}
                              className="h-7 px-2.5 text-[11px] font-bold bg-amber-500 hover:bg-amber-600 text-slate-950 dark:text-slate-950 shadow-sm gap-1"
                            >
                              <Zap className="h-3 w-3 fill-current" />
                              <span>Option</span>
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => openOrderModal(sig, false)}
                            className="h-7 px-2.5 text-[11px] font-semibold border-emerald-500/30 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/10 gap-1"
                          >
                            <TrendingUp className="h-3 w-3" />
                            <span>Equity</span>
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
        </div>
      </Card>

      {/* 1-Click Order Execution Confirmation Dialog */}
      <Dialog open={execModalOpen} onOpenChange={setExecModalOpen}>
        <DialogContent className="sm:max-w-[460px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-foreground">
              <Zap className="h-5 w-5 text-emerald-500" />
              <span>Confirm 1-Click Order Execution</span>
            </DialogTitle>
            <DialogDescription className="text-xs">
              Review parameters before routing the immediate market order through OpenAlgo.
            </DialogDescription>
          </DialogHeader>

          {selectedSignal && (
            <div className="space-y-4 py-2 text-xs">
              {/* Target Summary Card */}
              <div className="p-3.5 rounded-xl border bg-muted/40 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Contract / Asset:</span>
                  <span className="font-mono font-bold text-sm text-foreground">
                    {isOptionOrder && selectedSignal.option_recommendation
                      ? selectedSignal.option_recommendation.symbol
                      : selectedSignal.symbol}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Segment & Direction:</span>
                  <div className="flex items-center gap-1.5 font-bold font-mono">
                    <Badge variant="outline" className="text-[10px]">
                      {isOptionOrder ? 'NFO (Option)' : 'NSE (Equity)'}
                    </Badge>
                    <Badge className="bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 text-[10px]">
                      BUY (LONG)
                    </Badge>
                  </div>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Signal Trigger:</span>
                  <span className="font-mono text-muted-foreground">
                    #{selectedSignal.signal_id} ({selectedSignal.setup_type}) @{' '}
                    {formatCurrency(selectedSignal.spot_price)}
                  </span>
                </div>
              </div>

              {/* Order Parameters Form */}
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="order-qty" className="text-xs">
                    Quantity {isOptionOrder ? '(Units / Lots)' : '(Shares)'}
                  </Label>
                  <Input
                    id="order-qty"
                    type="number"
                    min={1}
                    value={orderQuantity}
                    onChange={(e) => setOrderQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                    className="h-8 text-xs font-mono"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="order-prod" className="text-xs">
                    Product Type
                  </Label>
                  <Select value={orderProduct} onValueChange={setOrderProduct}>
                    <SelectTrigger id="order-prod" className="h-8 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="MIS" className="text-xs">
                        MIS (Intraday)
                      </SelectItem>
                      <SelectItem value="NRML" className="text-xs">
                        NRML (Carry Forward)
                      </SelectItem>
                      <SelectItem value="CNC" className="text-xs">
                        CNC (Cash Delivery)
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {/* Risk:Reward Level Overview */}
              <div className="p-3 rounded-lg border bg-background/50 grid grid-cols-3 gap-2 text-center font-mono text-[11px]">
                <div className="space-y-0.5">
                  <div className="text-muted-foreground text-[10px]">Stop Loss</div>
                  <div className="text-rose-500 font-bold">
                    ₹
                    {isOptionOrder && selectedSignal.option_recommendation
                      ? selectedSignal.option_recommendation.opt_sl.toFixed(2)
                      : selectedSignal.sl.toFixed(2)}
                  </div>
                </div>
                <div className="space-y-0.5">
                  <div className="text-muted-foreground text-[10px]">Target 1 (1:1.5)</div>
                  <div className="text-cyan-500 font-bold">
                    ₹
                    {isOptionOrder && selectedSignal.option_recommendation
                      ? selectedSignal.option_recommendation.opt_tp1.toFixed(2)
                      : selectedSignal.tp1.toFixed(2)}
                  </div>
                </div>
                <div className="space-y-0.5">
                  <div className="text-muted-foreground text-[10px]">Target 2 (1:3.0)</div>
                  <div className="text-emerald-500 font-bold">
                    ₹
                    {isOptionOrder && selectedSignal.option_recommendation
                      ? selectedSignal.option_recommendation.opt_tp2.toFixed(2)
                      : selectedSignal.tp2.toFixed(2)}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 p-2.5 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-[11px]">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                <span>Order will route directly to active broker session with live WhatsApp broadcast.</span>
              </div>
            </div>
          )}

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setExecModalOpen(false)}
              disabled={isExecuting}
              className="text-xs"
            >
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleExecuteOrder}
              disabled={isExecuting}
              className="text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white gap-1.5"
            >
              {isExecuting ? (
                <>
                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                  <span>Routing Order...</span>
                </>
              ) : (
                <>
                  <Send className="h-3.5 w-3.5" />
                  <span>Confirm & Route Order</span>
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* WhatsApp 2-Way Desk Command Guide Modal */}
      <Dialog open={whatsappModalOpen} onOpenChange={setWhatsappModalOpen}>
        <DialogContent className="sm:max-w-[520px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-foreground">
              <MessageCircle className="h-5 w-5 text-emerald-500" />
              <span>Two-Way WhatsApp Execution Desk</span>
            </DialogTitle>
            <DialogDescription className="text-xs">
              Execute live trades and monitor signals straight from WhatsApp by replying to alerts.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2 text-xs">
            <div className="p-3 rounded-xl border bg-emerald-500/5 border-emerald-500/20 text-emerald-700 dark:text-emerald-300 space-y-1">
              <div className="font-semibold flex items-center gap-1.5">
                <Bot className="h-4 w-4" />
                <span>Instant Inbound Routing</span>
              </div>
              <p className="text-[11px] text-muted-foreground">
                When a signal is generated, OpenAlgo broadcasts the alert to your registered WhatsApp
                number. Simply reply with the short code within 5 minutes to trigger auto-order placement.
              </p>
            </div>

            <div className="space-y-2">
              <h4 className="font-semibold text-foreground text-xs uppercase tracking-wider">
                Available WhatsApp Commands
              </h4>

              <div className="space-y-2 font-mono text-[11px]">
                <div className="p-2.5 rounded-lg border bg-muted/40 flex items-center justify-between">
                  <div>
                    <span className="font-bold text-emerald-600 dark:text-emerald-400">BUY 101</span>
                    <span className="text-muted-foreground ml-2">(Buy 1 Lot recommended Option)</span>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0"
                    onClick={() => copyCommand('BUY 101')}
                  >
                    <Copy className="h-3 w-3" />
                  </Button>
                </div>

                <div className="p-2.5 rounded-lg border bg-muted/40 flex items-center justify-between">
                  <div>
                    <span className="font-bold text-amber-600 dark:text-amber-400">BUY 101 2L</span>
                    <span className="text-muted-foreground ml-2">(Buy 2 Lots Option)</span>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0"
                    onClick={() => copyCommand('BUY 101 2L')}
                  >
                    <Copy className="h-3 w-3" />
                  </Button>
                </div>

                <div className="p-2.5 rounded-lg border bg-muted/40 flex items-center justify-between">
                  <div>
                    <span className="font-bold text-cyan-600 dark:text-cyan-400">BUY EQ 101 50</span>
                    <span className="text-muted-foreground ml-2">(Buy 50 shares Cash Equity)</span>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0"
                    onClick={() => copyCommand('BUY EQ 101 50')}
                  >
                    <Copy className="h-3 w-3" />
                  </Button>
                </div>

                <div className="p-2.5 rounded-lg border bg-muted/40 flex items-center justify-between">
                  <div>
                    <span className="font-bold text-foreground">/signals</span>
                    <span className="text-muted-foreground ml-2">(List current active radar setups)</span>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0"
                    onClick={() => copyCommand('/signals')}
                  >
                    <Copy className="h-3 w-3" />
                  </Button>
                </div>

                <div className="p-2.5 rounded-lg border bg-muted/40 flex items-center justify-between">
                  <div>
                    <span className="font-bold text-foreground">/status</span>
                    <span className="text-muted-foreground ml-2">(Check broker session & radar health)</span>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0"
                    onClick={() => copyCommand('/status')}
                  >
                    <Copy className="h-3 w-3" />
                  </Button>
                </div>
              </div>
            </div>

            <div className="p-3 rounded-lg border bg-muted/30 text-[11px] text-muted-foreground">
              Configure your WhatsApp API credentials and webhooks in{' '}
              <Link to="/whatsapp" className="text-emerald-500 underline font-medium">
                WhatsApp Bot Settings
              </Link>
              .
            </div>
          </div>

          <DialogFooter>
            <Button
              type="button"
              size="sm"
              onClick={() => setWhatsappModalOpen(false)}
              className="text-xs"
            >
              Got It
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
