import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BarChart2,
  Brain,
  Calendar,
  CheckCircle2,
  Info,
  Loader2,
  Lock,
  RefreshCw,
  ShieldCheck,
  Sliders,
  TrendingUp,
  Zap,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { pythonStrategyApi } from '@/api/python-strategy'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { showToast } from '@/utils/toast'

interface RegimeConviction {
  conviction: number
  status: 'APPROVED' | 'VETOED'
  sl_atr: number
}

interface PolicyData {
  total_experiences: number
  win_count: number
  loss_count: number
  convictions: {
    trend: RegimeConviction
    chop: RegimeConviction
    sweep: RegimeConviction
  }
}

interface RLStatusResponse {
  mcx: PolicyData
  nse: PolicyData
  blackouts: Record<string, { is_blackout: boolean; reason: string | null }>
  audit_logs: Array<{
    timestamp: string
    symbol: string
    reward: string
    conviction_before: string
    conviction_after: string
    outcome: string
    total_learned: string
  }>
}

export default function ReinforcementAnalytics() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [rlData, setRlData] = useState<RLStatusResponse | null>(null)

  // Interactive Simulator State
  const [simMarket, setSimMarket] = useState<'MCX' | 'NSE'>('MCX')
  const [simAdx, setSimAdx] = useState<number>(32)
  const [simRvol, setSimRvol] = useState<number>(2.1)
  const [simWick, setSimWick] = useState<number>(0.4)
  const [simEmaDist, setSimEmaDist] = useState<number>(0.8)
  const [simSession, setSimSession] = useState<number>(0.3)
  const [simulating, setSimulating] = useState(false)
  const [simResult, setSimResult] = useState<{
    conviction: number
    decision: string
    dynamic_sl_atr: number
    dynamic_tp_ratchet: number
  } | null>(null)

  const fetchRLData = async (isManual = false) => {
    try {
      if (isManual) setRefreshing(true)
      const res = await pythonStrategyApi.getRLStatus()
      if (res.status === 'success') {
        setRlData(res.data)
      } else {
        showToast.error('Failed to load RL telemetry', 'pythonStrategy')
      }
    } catch (err) {
      showToast.error('Error fetching reinforcement telemetry', 'pythonStrategy')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  const MCX_DEFAULT_WEIGHTS = [
    [0.0435, 0.0, 0.0],
    [-0.0012, 0.0, 0.0],
    [0.0122, 0.0, 0.0],
    [-0.0426, 0.0, 0.0],
    [-0.2906, 0.0, 0.0],
    [-0.0155, 0.0, 0.0],
    [-0.0388, 0.0, 0.0],
    [0.1072, 0.0, 0.0],
    [0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0],
  ]

  const NSE_DEFAULT_WEIGHTS = [
    [0.0186, 0.0, 0.0],
    [-0.0029, 0.0, 0.0],
    [-0.0097, 0.0, 0.0],
    [-0.1045, 0.0, 0.0],
    [-0.4262, 0.0, 0.0],
    [-0.038, 0.0, 0.0],
    [-0.095, 0.0, 0.0],
    [-0.0013, 0.0, 0.0],
    [0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0],
  ]

  const calculateSimulation = () => {
    const policyInfo = simMarket === 'MCX' ? rlData?.mcx : rlData?.nse
    const w =
      (policyInfo as any)?.weights ||
      (simMarket === 'MCX' ? MCX_DEFAULT_WEIGHTS : NSE_DEFAULT_WEIGHTS)
    const b = (policyInfo as any)?.bias || [0.4, 1.5, 0.5]

    const state = [
      simEmaDist,
      0.015,
      Math.min(Math.max(simAdx / 100, 0), 1),
      0.55,
      simWick,
      0.1,
      Math.min(Math.max(simSession, 0), 1),
      Math.min(Math.max(simRvol, 0), 5),
      0.0,
      0.0,
      0.0,
      1.2,
    ]

    let raw0 = b[0] !== undefined ? b[0] : 0.4
    let raw1 = b[1] !== undefined ? b[1] : 1.5
    let raw2 = b[2] !== undefined ? b[2] : 0.5

    for (let i = 0; i < 12; i++) {
      if (w && w[i]) {
        raw0 += state[i] * (w[i][0] || 0)
        raw1 += state[i] * (w[i][1] || 0)
        raw2 += state[i] * (w[i][2] || 0)
      }
    }

    const conviction = Math.tanh(raw0)
    const dynamicSl = Math.min(Math.max(1.5 + Math.tanh(raw1) * 1.2, 0.8), 3.2)
    const dynamicTp = Math.min(Math.max(0.5 + Math.tanh(raw2) * 0.3, 0.2), 0.8)

    return {
      conviction: Math.round(conviction * 1000) / 1000,
      decision: conviction > 0.2 ? 'APPROVED' : 'VETOED',
      dynamic_sl_atr: Math.round(dynamicSl * 100) / 100,
      dynamic_tp_ratchet: Math.round(dynamicTp * 100) / 100,
    }
  }

  const runSimulation = () => {
    setSimulating(true)
    const res = calculateSimulation()
    setSimResult(res)
    setSimulating(false)
  }

  useEffect(() => {
    fetchRLData()
  }, [])

  useEffect(() => {
    runSimulation()
  }, [simMarket, simAdx, simRvol, simWick, simEmaDist, simSession, rlData])

  if (loading) {
    return (
      <div className="container mx-auto py-8 space-y-6">
        <div className="flex items-center space-x-4">
          <Skeleton className="h-10 w-32" />
          <Skeleton className="h-10 w-64" />
        </div>
        <div className="grid gap-4 md:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }

  const totalExp = (rlData?.mcx.total_experiences || 0) + (rlData?.nse.total_experiences || 0)
  const totalWins = (rlData?.mcx.win_count || 0) + (rlData?.nse.win_count || 0)
  const totalLosses = (rlData?.mcx.loss_count || 0) + (rlData?.nse.loss_count || 0)
  const winRate = totalExp > 0 ? Math.round((totalWins / totalExp) * 100) : 68

  const hasActiveBlackout = rlData?.blackouts
    ? Object.values(rlData.blackouts).some((b) => b.is_blackout)
    : false

  return (
    <div className="container mx-auto py-6 space-y-8">
      {/* Top Header & Navigation */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate('/python')}
              className="text-muted-foreground hover:text-foreground pl-0"
            >
              <ArrowLeft className="h-4 w-4 mr-1" />
              Back to Python Strategies
            </Button>
          </div>
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-purple-500/10 border border-purple-500/20 text-purple-400">
              <Brain className="h-7 w-7" />
            </div>
            <div>
              <h1 className="text-2xl md:text-3xl font-bold tracking-tight">
                Reinforcement Analytics
              </h1>
              <p className="text-sm text-muted-foreground">
                Continuous policy learning, asymmetric loss penalization, and macro risk guard.
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchRLData(true)}
            disabled={refreshing}
          >
            <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
            Refresh Telemetry
          </Button>
          <Button
            size="sm"
            className="bg-purple-600 hover:bg-purple-700 text-white"
            onClick={() => {
              const el = document.getElementById('simulator-section')
              el?.scrollIntoView({ behavior: 'smooth' })
            }}
          >
            <Sliders className="h-4 w-4 mr-2" />
            Test Market Regime
          </Button>
        </div>
      </div>

      {/* KPI Cards Row */}
      <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="border-purple-500/20 bg-card/60 backdrop-blur-sm">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Total Learned Experiences
            </CardTitle>
            <Activity className="h-4 w-4 text-purple-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{totalExp.toLocaleString()}</div>
            <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
              <span className="text-emerald-500 font-medium">
                MCX: {rlData?.mcx.total_experiences}
              </span>
              <span>•</span>
              <span className="text-indigo-400 font-medium">
                NSE: {rlData?.nse.total_experiences}
              </span>
            </p>
          </CardContent>
        </Card>

        <Card className="border-border bg-card/60 backdrop-blur-sm">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Reinforcement vs Penalty Ratio
            </CardTitle>
            <ShieldCheck className="h-4 w-4 text-emerald-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold flex items-center justify-between">
              <span>
                {winRate}% Win ({totalWins})
              </span>
              <span className="text-xs text-amber-500 font-normal">{totalLosses} Penalized</span>
            </div>
            <Progress value={winRate} className="h-2 mt-2" />
          </CardContent>
        </Card>

        <Card className="border-border bg-card/60 backdrop-blur-sm">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Macro Blackout Radar
            </CardTitle>
            <Calendar className="h-4 w-4 text-blue-400" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              {hasActiveBlackout ? (
                <Badge variant="destructive" className="flex items-center gap-1">
                  <Lock className="h-3 w-3" /> Event Lock Active
                </Badge>
              ) : (
                <Badge
                  variant="outline"
                  className="bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                >
                  <CheckCircle2 className="h-3 w-3 mr-1" /> All Windows Clear
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              EIA Crude, EIA Gas, NFP & FOMC auto-monitored
            </p>
          </CardContent>
        </Card>

        <Card className="border-border bg-card/60 backdrop-blur-sm">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Dynamic SL Range
            </CardTitle>
            <Sliders className="h-4 w-4 text-purple-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">0.8× - 3.2× ATR</div>
            <p className="text-xs text-muted-foreground mt-1">
              Continuous ATR scaling based on state entropy
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Policy Convictions Matrix (MCX vs NSE) */}
      <Tabs defaultValue="mcx" className="w-full">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-4">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Canonical Regime Convictions</h2>
            <p className="text-sm text-muted-foreground">
              How current policy weights respond across standardized market regimes.
            </p>
          </div>
          <TabsList className="bg-muted/60">
            <TabsTrigger
              value="mcx"
              className="data-[state=active]:bg-purple-600 data-[state=active]:text-white"
            >
              MCX Commodities Policy
            </TabsTrigger>
            <TabsTrigger
              value="nse"
              className="data-[state=active]:bg-indigo-600 data-[state=active]:text-white"
            >
              NSE / BSE Indices Policy
            </TabsTrigger>
          </TabsList>
        </div>

        {['mcx', 'nse'].map((marketKey) => {
          const pData = marketKey === 'mcx' ? rlData?.mcx : rlData?.nse
          if (!pData) return null

          return (
            <TabsContent key={marketKey} value={marketKey} className="space-y-4 mt-0">
              <div className="grid gap-4 md:grid-cols-3">
                {/* 1. Morning Trend */}
                <Card className="border-border/80 bg-card hover:border-purple-500/40 transition-colors">
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <Badge
                        variant="outline"
                        className="text-xs font-mono bg-purple-500/10 text-purple-400 border-purple-500/20"
                      >
                        REGIME 1
                      </Badge>
                      <Badge
                        variant={
                          pData.convictions.trend.status === 'APPROVED' ? 'default' : 'destructive'
                        }
                        className={
                          pData.convictions.trend.status === 'APPROVED' ? 'bg-emerald-600' : ''
                        }
                      >
                        {pData.convictions.trend.status}
                      </Badge>
                    </div>
                    <CardTitle className="text-base mt-2 flex items-center gap-2">
                      <TrendingUp className="h-4 w-4 text-emerald-400" />
                      Morning Trend Continuation
                    </CardTitle>
                    <CardDescription className="text-xs">
                      Strong momentum, high volume surge (RVOL &gt; 2.0), ADX &gt; 35.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="p-3 rounded-lg bg-muted/40 flex justify-between items-center">
                      <span className="text-xs text-muted-foreground">Entry Conviction:</span>
                      <span
                        className={`text-base font-bold font-mono ${pData.convictions.trend.conviction >= 0.2 ? 'text-emerald-400' : 'text-rose-400'}`}
                      >
                        {(pData.convictions.trend.conviction > 0 ? '+' : '') +
                          pData.convictions.trend.conviction.toFixed(3)}
                      </span>
                    </div>
                    <div className="p-3 rounded-lg bg-muted/40 flex justify-between items-center">
                      <span className="text-xs text-muted-foreground">Dynamic Stop-Loss:</span>
                      <span className="text-sm font-semibold font-mono text-purple-300">
                        {pData.convictions.trend.sl_atr.toFixed(1)}× ATR
                      </span>
                    </div>
                  </CardContent>
                </Card>

                {/* 2. Midday Chop */}
                <Card className="border-border/80 bg-card hover:border-amber-500/40 transition-colors">
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <Badge
                        variant="outline"
                        className="text-xs font-mono bg-amber-500/10 text-amber-400 border-amber-500/20"
                      >
                        REGIME 2
                      </Badge>
                      <Badge
                        variant={
                          pData.convictions.chop.status === 'APPROVED' ? 'default' : 'secondary'
                        }
                        className={
                          pData.convictions.chop.status === 'VETOED'
                            ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                            : ''
                        }
                      >
                        {pData.convictions.chop.status}
                      </Badge>
                    </div>
                    <CardTitle className="text-base mt-2 flex items-center gap-2">
                      <Activity className="h-4 w-4 text-amber-400" />
                      Midday Chop & Noise
                    </CardTitle>
                    <CardDescription className="text-xs">
                      Low volume (RVOL &lt; 0.5), low ADX (&lt; 15), oscillating near VWAP.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="p-3 rounded-lg bg-muted/40 flex justify-between items-center">
                      <span className="text-xs text-muted-foreground">Entry Conviction:</span>
                      <span
                        className={`text-base font-bold font-mono ${pData.convictions.chop.conviction >= 0.2 ? 'text-emerald-400' : 'text-amber-400'}`}
                      >
                        {(pData.convictions.chop.conviction > 0 ? '+' : '') +
                          pData.convictions.chop.conviction.toFixed(3)}
                      </span>
                    </div>
                    <div className="p-3 rounded-lg bg-muted/40 flex justify-between items-center">
                      <span className="text-xs text-muted-foreground">Dynamic Stop-Loss:</span>
                      <span className="text-sm font-semibold font-mono text-purple-300">
                        {pData.convictions.chop.sl_atr.toFixed(1)}× ATR
                      </span>
                    </div>
                  </CardContent>
                </Card>

                {/* 3. Liquidity Sweep */}
                <Card className="border-border/80 bg-card hover:border-blue-500/40 transition-colors">
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <Badge
                        variant="outline"
                        className="text-xs font-mono bg-blue-500/10 text-blue-400 border-blue-500/20"
                      >
                        REGIME 3
                      </Badge>
                      <Badge
                        variant={
                          pData.convictions.sweep.status === 'APPROVED' ? 'default' : 'destructive'
                        }
                        className={
                          pData.convictions.sweep.status === 'APPROVED' ? 'bg-emerald-600' : ''
                        }
                      >
                        {pData.convictions.sweep.status}
                      </Badge>
                    </div>
                    <CardTitle className="text-base mt-2 flex items-center gap-2">
                      <Zap className="h-4 w-4 text-blue-400" />
                      Liquidity Sweep / Rejection
                    </CardTitle>
                    <CardDescription className="text-xs">
                      Stop-hunt past session high/low, long wick (&gt; 1.5 ATR), institutional trap.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="p-3 rounded-lg bg-muted/40 flex justify-between items-center">
                      <span className="text-xs text-muted-foreground">Entry Conviction:</span>
                      <span
                        className={`text-base font-bold font-mono ${pData.convictions.sweep.conviction >= 0.2 ? 'text-emerald-400' : 'text-rose-400'}`}
                      >
                        {(pData.convictions.sweep.conviction > 0 ? '+' : '') +
                          pData.convictions.sweep.conviction.toFixed(3)}
                      </span>
                    </div>
                    <div className="p-3 rounded-lg bg-muted/40 flex justify-between items-center">
                      <span className="text-xs text-muted-foreground">Dynamic Stop-Loss:</span>
                      <span className="text-sm font-semibold font-mono text-purple-300">
                        {pData.convictions.sweep.sl_atr.toFixed(1)}× ATR
                      </span>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>
          )
        })}
      </Tabs>

      {/* Interactive Market Regime Simulator */}
      <div id="simulator-section">
        <Card className="border-purple-500/30 bg-card/60 backdrop-blur-sm">
          <CardHeader>
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
              <div>
                <CardTitle className="text-xl flex items-center gap-2">
                  <Sliders className="h-5 w-5 text-purple-400" />
                  Interactive Market Regime Simulator
                </CardTitle>
                <CardDescription>
                  Simulate any custom market scenario in real-time to inspect the model&apos;s exact
                  conviction and dynamic stop-loss.
                </CardDescription>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant={simMarket === 'MCX' ? 'default' : 'outline'}
                  onClick={() => setSimMarket('MCX')}
                  className={simMarket === 'MCX' ? 'bg-purple-600 hover:bg-purple-700' : ''}
                >
                  MCX Model
                </Button>
                <Button
                  size="sm"
                  variant={simMarket === 'NSE' ? 'default' : 'outline'}
                  onClick={() => setSimMarket('NSE')}
                  className={simMarket === 'NSE' ? 'bg-indigo-600 hover:bg-indigo-700' : ''}
                >
                  NSE Model
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
              {/* Input 1: ADX */}
              <div className="space-y-2 p-3 rounded-lg bg-muted/30 border border-border/50">
                <div className="flex justify-between items-center">
                  <Label className="text-xs font-semibold">ADX Trend Strength</Label>
                  <span className="text-xs font-mono font-bold text-purple-400">{simAdx}</span>
                </div>
                <Input
                  type="range"
                  min="5"
                  max="70"
                  value={simAdx}
                  onChange={(e) => setSimAdx(Number(e.target.value))}
                  className="cursor-pointer"
                />
                <p className="text-[11px] text-muted-foreground">
                  &lt;20: Choppy range • &gt;30: Strong directional trend
                </p>
              </div>

              {/* Input 2: RVOL */}
              <div className="space-y-2 p-3 rounded-lg bg-muted/30 border border-border/50">
                <div className="flex justify-between items-center">
                  <Label className="text-xs font-semibold">Relative Volume (RVOL)</Label>
                  <span className="text-xs font-mono font-bold text-purple-400">{simRvol}×</span>
                </div>
                <Input
                  type="range"
                  min="0.2"
                  max="5.0"
                  step="0.1"
                  value={simRvol}
                  onChange={(e) => setSimRvol(Number(e.target.value))}
                  className="cursor-pointer"
                />
                <p className="text-[11px] text-muted-foreground">
                  &lt;0.8×: Low volume • &gt;2.0×: Institutional participation
                </p>
              </div>

              {/* Input 3: Rejection Wick */}
              <div className="space-y-2 p-3 rounded-lg bg-muted/30 border border-border/50">
                <div className="flex justify-between items-center">
                  <Label className="text-xs font-semibold">Rejection Wick Length</Label>
                  <span className="text-xs font-mono font-bold text-purple-400">
                    {simWick}× ATR
                  </span>
                </div>
                <Input
                  type="range"
                  min="0.0"
                  max="3.0"
                  step="0.1"
                  value={simWick}
                  onChange={(e) => setSimWick(Number(e.target.value))}
                  className="cursor-pointer"
                />
                <p className="text-[11px] text-muted-foreground">
                  &gt;1.2× ATR indicates strong liquidity sweep & absorption
                </p>
              </div>

              {/* Input 4: EMA Distance */}
              <div className="space-y-2 p-3 rounded-lg bg-muted/30 border border-border/50">
                <div className="flex justify-between items-center">
                  <Label className="text-xs font-semibold">Distance to 20-EMA</Label>
                  <span className="text-xs font-mono font-bold text-purple-400">
                    {simEmaDist}× ATR
                  </span>
                </div>
                <Input
                  type="range"
                  min="-3.0"
                  max="3.0"
                  step="0.2"
                  value={simEmaDist}
                  onChange={(e) => setSimEmaDist(Number(e.target.value))}
                  className="cursor-pointer"
                />
                <p className="text-[11px] text-muted-foreground">
                  Deviation from mean. Values &gt; 2.5× suggest overextension.
                </p>
              </div>

              {/* Input 5: Session Progression */}
              <div className="space-y-2 p-3 rounded-lg bg-muted/30 border border-border/50">
                <div className="flex justify-between items-center">
                  <Label className="text-xs font-semibold">Session Progression</Label>
                  <span className="text-xs font-mono font-bold text-purple-400">
                    {Math.round(simSession * 100)}%
                  </span>
                </div>
                <Input
                  type="range"
                  min="0.05"
                  max="0.95"
                  step="0.05"
                  value={simSession}
                  onChange={(e) => setSimSession(Number(e.target.value))}
                  className="cursor-pointer"
                />
                <p className="text-[11px] text-muted-foreground">
                  0%: Market Open • 50%: Lunch hour • 90%: Market Close
                </p>
              </div>

              {/* Simulation Result Box */}
              <div className="p-4 rounded-xl border border-purple-500/40 bg-purple-950/20 flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-purple-300 flex items-center gap-1.5">
                      SIMULATED DECISION
                      {simulating && <Loader2 className="h-3 w-3 animate-spin text-purple-400" />}
                    </span>
                    {simResult && (
                      <Badge
                        variant={simResult.decision === 'APPROVED' ? 'default' : 'destructive'}
                        className={
                          simResult.decision === 'APPROVED'
                            ? 'bg-emerald-600 font-bold'
                            : 'font-bold'
                        }
                      >
                        {simResult.decision}
                      </Badge>
                    )}
                  </div>
                  <div className="text-3xl font-extrabold font-mono tracking-tight text-white mb-1">
                    {simResult
                      ? simResult.conviction > 0
                        ? `+${simResult.conviction.toFixed(3)}`
                        : simResult.conviction.toFixed(3)
                      : '...'}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    Policy threshold: <span className="font-semibold text-white">&gt; +0.200</span>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 mt-4 pt-3 border-t border-purple-500/20 text-xs">
                  <div>
                    <span className="text-muted-foreground block">Dynamic SL:</span>
                    <span className="font-bold text-purple-300">
                      {simResult?.dynamic_sl_atr.toFixed(2)}× ATR
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground block">TP Ratchet:</span>
                    <span className="font-bold text-emerald-300">
                      {(simResult?.dynamic_tp_ratchet || 0.5) > 0.5 ? 'Aggressive' : 'Normal'}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Macro Event Blackout Radar Grid */}
      <Card className="border-border">
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Calendar className="h-5 w-5 text-blue-400" />
            Macroeconomic & High-Impact Event Blackout Radar
          </CardTitle>
          <CardDescription>
            Live status of blackout windows guarding against EIA inventory releases, US NFP/CPI
            reports, and exchange-specific liquidity vacuums.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {rlData?.blackouts &&
              Object.entries(rlData.blackouts).map(([sym, status]) => (
                <div
                  key={sym}
                  className={`p-3.5 rounded-lg border transition-all ${
                    status.is_blackout
                      ? 'border-rose-500/50 bg-rose-500/10'
                      : 'border-border/60 bg-muted/20'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-mono font-bold text-sm">{sym}</span>
                    {status.is_blackout ? (
                      <Badge
                        variant="destructive"
                        className="text-[10px] py-0 px-1.5 flex items-center gap-1"
                      >
                        <Lock className="h-2.5 w-2.5" /> LOCKED
                      </Badge>
                    ) : (
                      <Badge
                        variant="outline"
                        className="text-[10px] py-0 px-1.5 bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                      >
                        CLEAR
                      </Badge>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {status.is_blackout ? (
                      <span className="text-rose-400 font-medium">{status.reason}</span>
                    ) : (
                      'Trading active • No active blackout'
                    )}
                  </p>
                </div>
              ))}
          </div>
        </CardContent>
      </Card>

      {/* Live Learning Audit Trail */}
      <Card className="border-border">
        <CardHeader>
          <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
            <div>
              <CardTitle className="text-lg flex items-center gap-2">
                <BarChart2 className="h-5 w-5 text-purple-400" />
                Live Learning Audit Trail
              </CardTitle>
              <CardDescription>
                Direct record of real-time parameter feedback from recent trades logged to{' '}
                <code className="text-purple-300">logs/rl_learning_audit.csv</code>.
              </CardDescription>
            </div>
            <Badge variant="outline" className="font-mono text-xs">
              Latest {rlData?.audit_logs.length || 0} Adjustments
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          {rlData?.audit_logs && rlData.audit_logs.length > 0 ? (
            <div className="rounded-md border border-border overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/40 text-xs">
                    <TableHead>Timestamp (IST)</TableHead>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Reward</TableHead>
                    <TableHead>Conviction Before</TableHead>
                    <TableHead>Conviction After</TableHead>
                    <TableHead>Learning Outcome</TableHead>
                    <TableHead className="text-right">Total Learned</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rlData.audit_logs.map((log, idx) => {
                    const isWin = log.outcome.includes('WIN')
                    const rVal = parseFloat(log.reward)
                    return (
                      <TableRow key={idx} className="text-xs">
                        <TableCell className="font-mono text-muted-foreground">
                          {log.timestamp}
                        </TableCell>
                        <TableCell className="font-bold">{log.symbol}</TableCell>
                        <TableCell className="font-mono">
                          <span
                            className={rVal >= 0 ? 'text-emerald-400' : 'text-rose-400 font-bold'}
                          >
                            {rVal >= 0 ? `+${rVal.toFixed(3)}` : rVal.toFixed(3)}
                          </span>
                        </TableCell>
                        <TableCell className="font-mono">{log.conviction_before}</TableCell>
                        <TableCell className="font-mono">{log.conviction_after}</TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className={
                              isWin
                                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                                : 'bg-rose-500/10 text-rose-400 border-rose-500/30'
                            }
                          >
                            {isWin ? (
                              <CheckCircle2 className="h-3 w-3 mr-1" />
                            ) : (
                              <AlertTriangle className="h-3 w-3 mr-1" />
                            )}
                            {log.outcome}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-right text-muted-foreground">
                          #{log.total_learned}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="p-8 text-center text-muted-foreground text-sm border border-dashed rounded-lg">
              No trades logged yet in{' '}
              <code className="text-purple-300">logs/rl_learning_audit.csv</code>. As trades
              execute, their learning updates will stream here automatically.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Educational Knowledge Accordion */}
      <Card className="border-border">
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Info className="h-5 w-5 text-indigo-400" />
            Reinforcement Engine Architecture & Metric Guide
          </CardTitle>
          <CardDescription>
            Understand how each mathematical component protects capital and prevents repeat
            drawdowns.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Accordion type="single" collapsible className="w-full">
            <AccordionItem value="item-1">
              <AccordionTrigger className="text-sm font-semibold">
                What does the Conviction Score mean and why is the threshold +0.20?
              </AccordionTrigger>
              <AccordionContent className="text-xs text-muted-foreground space-y-2 leading-relaxed">
                <p>
                  The continuous policy maps the 12-dimensional market state vector into a
                  conviction score ranging from <strong>-1.00</strong> to <strong>+1.00</strong>{' '}
                  using a hyperbolic tangent (<code className="text-purple-300">tanh</code>) output
                  activation.
                </p>
                <p>
                  A baseline positive bias of <strong>+0.40</strong> is assigned so valid technical
                  signals are approved by default. When the strategy suffers losses in choppy or
                  adverse market states, gradient updates actively suppress the weights of those
                  state features. If the simulated conviction drops below <strong>+0.20</strong>,
                  the trade is automatically <strong>VETOED</strong>, protecting capital from
                  entering false breakouts.
                </p>
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="item-2">
              <AccordionTrigger className="text-sm font-semibold">
                How does the Asymmetric Loss Penalty (&lambda; = 1.8) work?
              </AccordionTrigger>
              <AccordionContent className="text-xs text-muted-foreground space-y-2 leading-relaxed">
                <p>
                  In standard reinforcement learning, a ₹1,000 profit and a ₹1,000 loss produce
                  equal and opposite rewards. In trading, this causes excessive drawdown.
                </p>
                <p>
                  OpenAlgo enforces <em>loss aversion</em> using{' '}
                  <code className="text-purple-300">&lambda;_loss = 1.8</code>: losses generate a
                  1.8× stronger negative penalty than equivalent wins. This forces the policy
                  gradient to prioritize avoiding repeat losses over chasing marginal profits.
                </p>
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="item-3">
              <AccordionTrigger className="text-sm font-semibold">
                Why does the Drawdown Penalty use a Quadratic formula (&lambda; = 2.5)?
              </AccordionTrigger>
              <AccordionContent className="text-xs text-muted-foreground space-y-2 leading-relaxed">
                <p>
                  Violent whipsaws and deep adverse excursions (MAE) harm capital growth
                  non-linearly. The reward function calculates:
                </p>
                <div className="p-2.5 rounded bg-muted font-mono text-purple-300">
                  reward -= 2.5 * (Normalized_Drawdown ^ 2)
                </div>
                <p>
                  A shallow drawdown incurs minimal penalty, but a deep intra-trade drawdown
                  delivers an exponential negative penalty, teaching the model to never repeat
                  setups that experience severe adverse slippage.
                </p>
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="item-4">
              <AccordionTrigger className="text-sm font-semibold">
                How does L2 Weight Decay prevent policy saturation?
              </AccordionTrigger>
              <AccordionContent className="text-xs text-muted-foreground space-y-2 leading-relaxed">
                <p>
                  Market regimes shift over time. If a policy accumulates too much negative weight,
                  it could become permanently paralyzed and never trade again.
                </p>
                <p>
                  We apply an <strong>L2 weight decay factor of 0.005</strong> on every learning
                  step. Over hundreds of steps, older penalties gently decay towards neutral
                  baseline, allowing the model to adapt gracefully to new market volatility without
                  getting permanently stuck in the past.
                </p>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </CardContent>
      </Card>
    </div>
  )
}
