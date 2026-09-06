import {
  BarChart3,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  Download,
  Filter,
  Flame,
  Layers,
  Loader2,
  RefreshCw,
  Search,
  Sliders,
  Sparkles,
  Trophy,
  X,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn, makeFormatCurrency, sanitizeCSV } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { onModeChange } from '@/stores/themeStore'
import type { StrategyAnalyticsResponse, StrategyPerformanceMetric } from '@/types/trading'
import { showToast } from '@/utils/toast'
import { webClient } from '@/api/client'

type TimeframeOption = string

interface TimeframeMeta {
  id: string
  label: string
  description: string
}

const PRESET_TIMEFRAMES: TimeframeMeta[] = [
  { id: '1D', label: 'Today (1D)', description: "Today's live session P&L" },
  { id: '2D', label: '2 Days (2D)', description: 'Last 48 hours rolling performance' },
  { id: '1W', label: '1 Week (1W)', description: 'Last 7 days performance' },
  { id: '2W', label: '2 Weeks (2W)', description: 'Last 14 days performance' },
  { id: '1M', label: '1 Month (1M)', description: 'Last 30 days performance' },
  { id: 'ALL', label: 'All-Time', description: 'Cumulative historical ledger' },
]

const QUICK_CUSTOM_PRESETS = [
  { id: '3D', label: '3 Days (3D)' },
  { id: '4D', label: '4 Days (4D)' },
  { id: '5D', label: '5 Days (5D)' },
  { id: '3W', label: '3 Weeks (3W)' },
  { id: '4W', label: '4 Weeks (4W)' },
]

function formatTimeframeDisplay(tf: string): string {
  const preset = PRESET_TIMEFRAMES.find((p) => p.id === tf)
  if (preset) return preset.label
  const quick = QUICK_CUSTOM_PRESETS.find((q) => q.id === tf)
  if (quick) return quick.label
  const match = tf.match(/^(\d+)([DWM])$/i)
  if (match) {
    const val = match[1]
    const unit = match[2].toUpperCase()
    const unitWord =
      unit === 'D'
        ? val === '1' ? 'Day' : 'Days'
        : unit === 'W'
        ? val === '1' ? 'Week' : 'Weeks'
        : val === '1' ? 'Month' : 'Months'
    return `${val} ${unitWord} (${tf.toUpperCase()})`
  }
  return tf
}

export default function StrategyAnalytics() {
  const { user } = useAuthStore()
  const formatCurrency = useMemo(() => makeFormatCurrency(user?.broker), [user?.broker])

  const [timeframe, setTimeframe] = useState<TimeframeOption>('1D')
  const [customNum, setCustomNum] = useState<number>(3)
  const [customUnit, setCustomUnit] = useState<'D' | 'W' | 'M'>('D')
  const [isCustomOpen, setIsCustomOpen] = useState(false)

  const isCustomActive = useMemo(() => {
    return !PRESET_TIMEFRAMES.some((p) => p.id === timeframe)
  }, [timeframe])

  const [analyticsData, setAnalyticsData] = useState<StrategyAnalyticsResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [onlyActive, setOnlyActive] = useState(true)

  const fetchAnalytics = useCallback(
    async (showRefresh = false) => {
      if (showRefresh) setIsRefreshing(true)
      try {
        const res = await webClient.get(`/api/strategy-analytics?timeframe=${timeframe}`)
        if (res.data && res.data.status === 'success') {
          setAnalyticsData(res.data)
        } else {
          // Fallback to strategy route
          const resFallback = await webClient.get(`/strategy/api/analytics?timeframe=${timeframe}`)
          if (resFallback.data && resFallback.data.status === 'success') {
            setAnalyticsData(resFallback.data)
          }
        }
      } catch (err) {
        console.error('Failed to fetch strategy analytics:', err)
      } finally {
        setIsLoading(false)
        setIsRefreshing(false)
      }
    },
    [timeframe]
  )

  useEffect(() => {
    setIsLoading(true)
    fetchAnalytics()
  }, [fetchAnalytics])

  useEffect(() => {
    return onModeChange(() => {
      fetchAnalytics()
    })
  }, [fetchAnalytics])

  const allStrategies: StrategyPerformanceMetric[] = useMemo(() => {
    if (!analyticsData?.strategies) return []
    return Object.values(analyticsData.strategies).sort((a, b) => b.total_pnl - a.total_pnl)
  }, [analyticsData])

  // Filtered strategies according to search and active toggle
  const strategyList: StrategyPerformanceMetric[] = useMemo(() => {
    return allStrategies.filter((s) => {
      // 1. Filter out inactive / 0-trade strategies if onlyActive is enabled
      if (onlyActive) {
        const hasActivity =
          s.has_activity ??
          (s.total_trades > 0 || Math.abs(s.total_pnl) > 0.001 || s.active_positions_count > 0)
        if (!hasActivity) return false
      }
      // 2. Search query filter
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim()
        if (!s.strategy.toLowerCase().includes(q)) return false
      }
      return true
    })
  }, [allStrategies, onlyActive, searchQuery])

  const activeStrategiesCount = useMemo(() => {
    return allStrategies.filter(
      (s) =>
        s.has_activity ??
        (s.total_trades > 0 || Math.abs(s.total_pnl) > 0.001 || s.active_positions_count > 0)
    ).length
  }, [allStrategies])

  // Summary computed for currently visible/active strategies
  const summary = useMemo(() => {
    const targetList = onlyActive ? allStrategies.filter((s) => s.has_activity ?? (s.total_trades > 0 || Math.abs(s.total_pnl) > 0.001 || s.active_positions_count > 0)) : allStrategies
    const totalPnl = targetList.reduce((acc, s) => acc + (s.total_pnl || 0), 0)
    const totalTrades = targetList.reduce((acc, s) => acc + (s.total_trades || 0), 0)
    const winningCount = targetList.filter((s) => (s.total_pnl || 0) > 0).length
    const losingCount = targetList.filter((s) => (s.total_pnl || 0) < 0).length

    const positiveStrats = targetList.filter((s) => (s.total_pnl || 0) > 0)
    const topPerformer =
      positiveStrats.length > 0
        ? positiveStrats[0]
        : targetList.length > 0
        ? targetList[0]
        : null

    return {
      total_pnl: totalPnl,
      total_trades: totalTrades,
      active_count: targetList.length,
      winning_count: winningCount,
      losing_count: losingCount,
      top_performer: topPerformer,
    }
  }, [allStrategies, onlyActive])

  const exportAnalyticsCSV = () => {
    if (strategyList.length === 0) {
      showToast.error('No strategy data to export', 'system')
      return
    }

    try {
      const headers = [
        'Strategy',
        'Timeframe',
        'Realized PnL',
        'Unrealized PnL',
        'Total PnL',
        'Total Trades',
        'Win Rate (%)',
        'Profit Factor',
        'Max Drawdown',
        'Avg Trade PnL',
        'Active Legs',
      ]
      const rows = strategyList.map((s) => [
        sanitizeCSV(s.strategy),
        sanitizeCSV(s.timeframe),
        sanitizeCSV(s.realized_pnl),
        sanitizeCSV(s.unrealized_pnl),
        sanitizeCSV(s.total_pnl),
        sanitizeCSV(s.total_trades),
        sanitizeCSV(s.win_rate),
        sanitizeCSV(s.profit_factor),
        sanitizeCSV(s.max_drawdown),
        sanitizeCSV(s.avg_trade_pnl),
        sanitizeCSV(s.active_positions_count),
      ])

      const csv = [headers, ...rows].map((row) => row.join(',')).join('\n')
      const blob = new Blob([csv], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const filename = `strategy_analytics_${timeframe}_${new Date().toISOString().split('T')[0]}.csv`
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      showToast.success(`Exported ${filename}`, 'clipboard')
    } catch {
      showToast.error('Failed to export analytics CSV', 'system')
    }
  }

  return (
    <div className="space-y-6 pb-12">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-3xl font-bold tracking-tight">Strategy P&L Analytics</h1>
            <Badge variant="outline" className="bg-indigo-500/10 text-indigo-600 border-indigo-500/30 gap-1">
              <Sparkles className="h-3 w-3" /> Multi-Timeframe
            </Badge>
          </div>
          <p className="text-muted-foreground text-sm mt-1">
            Realized & unrealized P&L, win rates, and metrics across active and historical algorithmic strategies.
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchAnalytics(true)}
            disabled={isRefreshing}
            className="h-9 gap-1.5"
          >
            <RefreshCw className={cn('h-4 w-4', isRefreshing && 'animate-spin')} />
            Refresh
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={exportAnalyticsCSV}
            disabled={strategyList.length === 0}
            className="h-9 gap-1.5"
          >
            <Download className="h-4 w-4" />
            Export CSV
          </Button>
        </div>
      </div>

      {/* Filter and Timeframe Selector Bar */}
      <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3 bg-muted/40 p-3 rounded-xl border">
        {/* Search & Active Filter */}
        <div className="flex items-center gap-2 flex-1 max-w-md">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search strategy..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-8 pr-8 h-9 text-xs"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          <Button
            variant={onlyActive ? 'default' : 'outline'}
            size="sm"
            onClick={() => setOnlyActive(!onlyActive)}
            className={cn('h-9 text-xs gap-1.5 shrink-0', onlyActive ? 'bg-primary text-primary-foreground' : '')}
          >
            <Filter className="h-3.5 w-3.5" />
            {onlyActive ? 'Active / Traded' : 'All Strategies'}
          </Button>
        </div>

        {/* Timeframe Buttons */}
        <div className="flex items-center gap-1.5 flex-wrap justify-end">
          <Clock className="h-3.5 w-3.5 text-muted-foreground mr-1 hidden sm:inline" />
          {PRESET_TIMEFRAMES.map((tf) => (
            <Button
              key={tf.id}
              variant={timeframe === tf.id ? 'default' : 'ghost'}
              size="sm"
              onClick={() => setTimeframe(tf.id)}
              className={cn(
                'rounded-lg text-xs font-medium transition-all h-8',
                timeframe === tf.id && 'bg-primary text-primary-foreground shadow-sm'
              )}
            >
              {tf.label}
            </Button>
          ))}

          {/* Custom Days / Weeks Popover */}
          <Popover open={isCustomOpen} onOpenChange={setIsCustomOpen}>
            <PopoverTrigger asChild>
              <Button
                variant={isCustomActive ? 'default' : 'outline'}
                size="sm"
                className={cn(
                  'rounded-lg text-xs font-medium transition-all h-8 gap-1.5',
                  isCustomActive && 'bg-primary text-primary-foreground shadow-sm'
                )}
              >
                <CalendarDays className="h-3.5 w-3.5" />
                <span>{isCustomActive ? formatTimeframeDisplay(timeframe) : 'Custom Days / Weeks'}</span>
                <ChevronDown className="h-3 w-3 opacity-60" />
              </Button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              className="w-80 p-3.5 space-y-3 bg-popover text-popover-foreground border shadow-xl rounded-xl z-50"
            >
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                  <Sliders className="h-3.5 w-3.5 text-primary" />
                  Custom Timeframe
                </h4>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  Select predefined days/weeks or specify any custom duration
                </p>
              </div>

              {/* Quick Picks */}
              <div>
                <span className="text-[11px] font-medium text-foreground/80 block mb-1.5">Quick Picks</span>
                <div className="flex flex-wrap gap-1.5">
                  {QUICK_CUSTOM_PRESETS.map((q) => (
                    <Button
                      key={q.id}
                      variant={timeframe === q.id ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => {
                        setTimeframe(q.id)
                        setIsCustomOpen(false)
                      }}
                      className={cn(
                        'h-7 px-2.5 text-xs rounded-md',
                        timeframe === q.id && 'bg-primary text-primary-foreground font-semibold'
                      )}
                    >
                      {q.label}
                    </Button>
                  ))}
                </div>
              </div>

              {/* Custom Input */}
              <div className="border-t pt-2.5 space-y-2">
                <span className="text-[11px] font-medium text-foreground/80 block">Specify Duration</span>
                <div className="flex items-center gap-2">
                  <Input
                    type="number"
                    min={1}
                    max={365}
                    value={customNum || ''}
                    onChange={(e) => {
                      const val = parseInt(e.target.value, 10)
                      setCustomNum(isNaN(val) ? 1 : Math.max(1, Math.min(365, val)))
                    }}
                    placeholder="e.g. 3"
                    className="h-8 text-xs w-20 font-mono text-center"
                  />

                  <div className="flex rounded-md border p-0.5 bg-muted/30 flex-1">
                    <button
                      type="button"
                      onClick={() => setCustomUnit('D')}
                      className={cn(
                        'flex-1 py-1 text-xs rounded transition-colors text-center font-medium',
                        customUnit === 'D'
                          ? 'bg-background shadow-xs text-foreground font-semibold'
                          : 'text-muted-foreground hover:text-foreground'
                      )}
                    >
                      Days
                    </button>
                    <button
                      type="button"
                      onClick={() => setCustomUnit('W')}
                      className={cn(
                        'flex-1 py-1 text-xs rounded transition-colors text-center font-medium',
                        customUnit === 'W'
                          ? 'bg-background shadow-xs text-foreground font-semibold'
                          : 'text-muted-foreground hover:text-foreground'
                      )}
                    >
                      Weeks
                    </button>
                    <button
                      type="button"
                      onClick={() => setCustomUnit('M')}
                      className={cn(
                        'flex-1 py-1 text-xs rounded transition-colors text-center font-medium',
                        customUnit === 'M'
                          ? 'bg-background shadow-xs text-foreground font-semibold'
                          : 'text-muted-foreground hover:text-foreground'
                      )}
                    >
                      Months
                    </button>
                  </div>
                </div>

                <Button
                  size="sm"
                  onClick={() => {
                    const cleanNum = Math.max(1, customNum || 1)
                    const tf = `${cleanNum}${customUnit}`
                    setTimeframe(tf)
                    setIsCustomOpen(false)
                  }}
                  className="w-full h-8 text-xs gap-1.5 font-medium mt-1"
                >
                  <Check className="h-3.5 w-3.5" />
                  Apply {customNum || 1} {customUnit === 'D' ? (customNum === 1 ? 'Day' : 'Days') : customUnit === 'W' ? (customNum === 1 ? 'Week' : 'Weeks') : (customNum === 1 ? 'Month' : 'Months')}
                </Button>
              </div>
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {/* Executive Metric Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {/* Total Portfolio PnL */}
        <Card className="border-l-4 border-l-primary shadow-sm hover:shadow-md transition-shadow">
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between text-xs font-medium">
              <span>Portfolio Net P&L ({timeframe})</span>
              <BarChart3 className="h-4 w-4 text-muted-foreground" />
            </CardDescription>
            <CardTitle
              className={cn(
                'text-2xl font-bold font-mono tracking-tight',
                summary.total_pnl >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'
              )}
            >
              {summary.total_pnl >= 0 ? '+' : ''}
              {formatCurrency(summary.total_pnl)}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0 text-xs text-muted-foreground">
            {summary.winning_count} Winning / {summary.losing_count} Losing Strategies
          </CardContent>
        </Card>

        {/* Top Alpha Strategy */}
        <Card className="border-l-4 border-l-amber-500 shadow-sm hover:shadow-md transition-shadow">
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between text-xs font-medium">
              <span>Top Alpha Strategy</span>
              <Trophy className="h-4 w-4 text-amber-500" />
            </CardDescription>
            <CardTitle className="text-xl font-bold truncate text-foreground">
              {summary.top_performer ? summary.top_performer.strategy : 'None'}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0 text-xs text-muted-foreground flex items-center gap-1 font-mono">
            {summary.top_performer && summary.top_performer.total_pnl > 0 ? (
              <span className="text-green-600 dark:text-green-400 font-semibold">
                +{formatCurrency(summary.top_performer.total_pnl)}
              </span>
            ) : summary.top_performer ? (
              <span className={summary.top_performer.total_pnl >= 0 ? 'text-green-600 dark:text-green-400 font-semibold' : 'text-red-600 dark:text-red-400 font-semibold'}>
                {summary.top_performer.total_pnl >= 0 ? '+' : ''}{formatCurrency(summary.top_performer.total_pnl)}
              </span>
            ) : (
              'Awaiting trades'
            )}
          </CardContent>
        </Card>

        {/* Total Strategy Trades */}
        <Card className="border-l-4 border-l-blue-500 shadow-sm hover:shadow-md transition-shadow">
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between text-xs font-medium">
              <span>Total Strategy Trades</span>
              <Zap className="h-4 w-4 text-blue-500" />
            </CardDescription>
            <CardTitle className="text-2xl font-bold font-mono text-foreground">
              {summary.total_trades}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0 text-xs text-muted-foreground">
            Executed across {summary.active_count} strategies
          </CardContent>
        </Card>

        {/* Tracked Strategies */}
        <Card className="border-l-4 border-l-purple-500 shadow-sm hover:shadow-md transition-shadow">
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center justify-between text-xs font-medium">
              <span>Tracked Strategies</span>
              <Layers className="h-4 w-4 text-purple-500" />
            </CardDescription>
            <CardTitle className="text-2xl font-bold font-mono text-foreground">
              {activeStrategiesCount}
              <span className="text-sm font-normal text-muted-foreground ml-1.5">
                / {allStrategies.length} Total
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0 text-xs text-muted-foreground">
            {onlyActive ? 'Showing active/traded only' : 'Showing all tracked strategies'}
          </CardContent>
        </Card>
      </div>

      {/* Comparative Performance Matrix Table */}
      <Card className="shadow-sm">
        <CardHeader className="pb-3 border-b">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div>
              <CardTitle className="text-lg font-bold flex items-center gap-2">
                <Flame className="h-5 w-5 text-indigo-500" />
                Strategy Performance Leaderboard ({timeframe})
              </CardTitle>
              <CardDescription className="text-xs mt-0.5">
                Full breakdown of Realized P&L, Unrealized MTM, Win Rate, and Drawdown per strategy. Click any row to inspect legs.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="font-mono text-xs">
                {strategyList.length} {onlyActive ? 'Active Strategies' : 'Strategies Listed'}
              </Badge>
            </div>
          </div>
        </CardHeader>

        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
          ) : strategyList.length === 0 ? (
            <EmptyState
              icon={Sliders}
              title="No matching strategy performance data"
              description={
                onlyActive
                  ? `No active trades or positions in the ${timeframe} window. Switch to 'All Strategies' to view flat historical strategies.`
                  : `No executed orders recorded for the ${timeframe} timeframe window.`
              }
            />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/30">
                    <TableHead className="font-semibold text-foreground">Strategy Name</TableHead>
                    <TableHead className="text-right font-semibold text-foreground">Realized P&L</TableHead>
                    <TableHead className="text-right font-semibold text-foreground">Unrealized MTM</TableHead>
                    <TableHead className="text-right font-semibold text-foreground">Total Net P&L</TableHead>
                    <TableHead className="text-right font-semibold text-foreground">Trades</TableHead>
                    <TableHead className="text-right font-semibold text-foreground">Win Rate</TableHead>
                    <TableHead className="text-right font-semibold text-foreground">Profit Factor</TableHead>
                    <TableHead className="text-right font-semibold text-foreground">Max DD</TableHead>
                    <TableHead className="text-right font-semibold text-foreground">Avg / Trade</TableHead>
                    <TableHead className="text-center font-semibold text-foreground">Open Legs</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {strategyList.map((s, idx) => {
                    const isNetProfit = s.total_pnl > 0
                    const isNetLoss = s.total_pnl < 0
                    const isSelected = selectedStrategy === s.strategy

                    return (
                      <>
                        <TableRow
                          key={s.strategy}
                          className={cn(
                            'cursor-pointer transition-colors',
                            isSelected && 'bg-indigo-500/10 font-medium',
                            idx === 0 && isNetProfit && 'bg-green-500/[0.03]'
                          )}
                          onClick={() => setSelectedStrategy(isSelected ? null : s.strategy)}
                        >
                          <TableCell className="font-semibold flex items-center gap-2">
                            {idx === 0 && isNetProfit && <Trophy className="h-4 w-4 text-amber-500 shrink-0" />}
                            <Badge
                              variant="secondary"
                              className={cn(
                                'font-medium text-xs border whitespace-nowrap',
                                isNetProfit
                                  ? 'bg-green-500/10 text-green-700 dark:text-green-400 border-green-500/20'
                                  : isNetLoss
                                  ? 'bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20'
                                  : 'bg-muted text-muted-foreground border-border'
                              )}
                            >
                              {s.strategy}
                            </Badge>
                            {isSelected ? (
                              <ChevronUp className="h-3.5 w-3.5 text-muted-foreground ml-auto" />
                            ) : (
                              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground ml-auto opacity-40" />
                            )}
                          </TableCell>
                          <TableCell className="text-right font-mono text-sm">
                            <span
                              className={
                                s.realized_pnl > 0
                                  ? 'text-green-600 dark:text-green-400'
                                  : s.realized_pnl < 0
                                  ? 'text-red-600 dark:text-red-400'
                                  : 'text-muted-foreground'
                              }
                            >
                              {s.realized_pnl > 0 ? '+' : ''}
                              {formatCurrency(s.realized_pnl)}
                            </span>
                          </TableCell>
                          <TableCell className="text-right font-mono text-sm">
                            <span
                              className={
                                s.unrealized_pnl > 0
                                  ? 'text-green-600 dark:text-green-400'
                                  : s.unrealized_pnl < 0
                                  ? 'text-red-600 dark:text-red-400'
                                  : 'text-muted-foreground'
                              }
                            >
                              {s.unrealized_pnl > 0 ? '+' : ''}
                              {formatCurrency(s.unrealized_pnl)}
                            </span>
                          </TableCell>
                          <TableCell className="text-right font-mono font-bold text-sm">
                            <span
                              className={cn(
                                'px-2 py-0.5 rounded',
                                isNetProfit
                                  ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                                  : isNetLoss
                                  ? 'bg-red-500/10 text-red-600 dark:text-red-400'
                                  : 'text-muted-foreground'
                              )}
                            >
                              {s.total_pnl > 0 ? '+' : ''}
                              {formatCurrency(s.total_pnl)}
                            </span>
                          </TableCell>
                          <TableCell className="text-right font-mono text-sm font-semibold">
                            {s.total_trades}
                          </TableCell>
                          <TableCell className="text-right font-mono text-sm">
                            <Badge variant="outline" className="font-mono text-xs">
                              {s.win_rate.toFixed(1)}%
                            </Badge>
                          </TableCell>
                          <TableCell className="text-right font-mono text-sm">
                            {s.profit_factor > 0 ? s.profit_factor.toFixed(2) : '-'}
                          </TableCell>
                          <TableCell className="text-right font-mono text-sm text-red-500">
                            {s.max_drawdown !== 0 ? formatCurrency(s.max_drawdown) : '₹0.00'}
                          </TableCell>
                          <TableCell className="text-right font-mono text-sm">
                            <span
                              className={
                                s.avg_trade_pnl > 0
                                  ? 'text-green-600 dark:text-green-400'
                                  : s.avg_trade_pnl < 0
                                  ? 'text-red-600 dark:text-red-400'
                                  : 'text-muted-foreground'
                              }
                            >
                              {s.avg_trade_pnl > 0 ? '+' : ''}
                              {formatCurrency(s.avg_trade_pnl)}
                            </span>
                          </TableCell>
                          <TableCell className="text-center">
                            {s.active_positions_count > 0 ? (
                              <Badge variant="default" className="bg-indigo-600 font-mono text-xs">
                                {s.active_positions_count} Active
                              </Badge>
                            ) : (
                              <span className="text-muted-foreground text-xs font-mono">Flat</span>
                            )}
                          </TableCell>
                        </TableRow>

                        {/* Expanded Strategy Legs Details Row */}
                        {isSelected && s.legs && s.legs.length > 0 && (
                          <TableRow className="bg-muted/20 hover:bg-muted/20">
                            <TableCell colSpan={10} className="p-4">
                              <div className="rounded-lg border bg-background p-3 space-y-2">
                                <div className="flex items-center justify-between">
                                  <span className="text-xs font-bold text-foreground">
                                    Leg Breakdown for {s.strategy} ({s.legs.length} legs tracked)
                                  </span>
                                </div>
                                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                                  {s.legs.map((leg: any, lIdx: number) => {
                                    const legQty = leg.quantity || 0
                                    const legRealized = leg.realized || 0
                                    const isFlat = Math.abs(legQty) < 1e-9

                                    return (
                                      <div
                                        key={lIdx}
                                        className="rounded border p-2.5 bg-muted/10 space-y-1 text-xs font-mono"
                                      >
                                        <div className="flex items-center justify-between font-bold">
                                          <span className="truncate">{leg.symbol}</span>
                                          <Badge
                                            variant="outline"
                                            className={cn('text-[10px] h-4', isFlat ? 'text-muted-foreground' : 'text-indigo-500 border-indigo-500/30')}
                                          >
                                            {isFlat ? 'FLAT' : `${legQty} QTY`}
                                          </Badge>
                                        </div>
                                        <div className="flex items-center justify-between text-muted-foreground text-[11px]">
                                          <span>{leg.exchange} • {leg.product}</span>
                                          <span>Avg: {formatCurrency(leg.average_price || 0)}</span>
                                        </div>
                                        <div className="flex items-center justify-between pt-1 border-t text-[11px]">
                                          <span className="text-muted-foreground">Realized P&L:</span>
                                          <span
                                            className={cn(
                                              'font-bold',
                                              legRealized > 0
                                                ? 'text-green-600 dark:text-green-400'
                                                : legRealized < 0
                                                ? 'text-red-600 dark:text-red-400'
                                                : 'text-muted-foreground'
                                            )}
                                          >
                                            {legRealized > 0 ? '+' : ''}
                                            {formatCurrency(legRealized)}
                                          </span>
                                        </div>
                                      </div>
                                    )
                                  })}
                                </div>
                              </div>
                            </TableCell>
                          </TableRow>
                        )}
                      </>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

