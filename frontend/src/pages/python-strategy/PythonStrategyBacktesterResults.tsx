import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ExternalLink, Lightbulb, Activity, FileSpreadsheet } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { usePythonStrategyBacktestStore } from '@/stores/pythonStrategyBacktestStore'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'

export default function PythonStrategyBacktesterResults() {
  const navigate = useNavigate()
  const { result } = usePythonStrategyBacktestStore()

  useEffect(() => {
    if (!result) {
      navigate('/tools/python-backtester')
    }
  }, [result, navigate])

  if (!result) return null

  const { metrics, optimization, tearsheet_url, parameters, symbol } = result

  return (
    <div className="container py-8 max-w-5xl mx-auto space-y-8">
      <div className="flex items-center gap-4">
        <Button variant="outline" size="icon" onClick={() => navigate('/tools/python-backtester')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Backtest Results: {symbol}</h1>
          <p className="text-muted-foreground">
            Python Strategy Execution Summary
          </p>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle>Performance Metrics</CardTitle>
                <CardDescription>VectorBT Simulation Output</CardDescription>
              </div>
              <Activity className="h-5 w-5 text-muted-foreground" />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Total Return</p>
                <p className="text-2xl font-bold text-green-600">
                  {metrics['Total Return [%]'] ? `${Number(metrics['Total Return [%]']).toFixed(2)}%` : 'N/A'}
                </p>
              </div>
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Max Drawdown</p>
                <p className="text-2xl font-bold text-red-600">
                  {metrics['Max Drawdown [%]'] ? `${Number(metrics['Max Drawdown [%]']).toFixed(2)}%` : 'N/A'}
                </p>
              </div>
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Win Rate</p>
                <p className="text-xl font-medium">
                  {metrics['Win Rate [%]'] ? `${Number(metrics['Win Rate [%]']).toFixed(2)}%` : 'N/A'}
                </p>
              </div>
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Sharpe Ratio</p>
                <p className="text-xl font-medium">
                  {metrics['Sharpe Ratio'] ? Number(metrics['Sharpe Ratio']).toFixed(2) : 'N/A'}
                </p>
              </div>
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Total Trades</p>
                <p className="text-xl font-medium">{metrics['Total Trades'] || 0}</p>
              </div>
              <div className="space-y-1">
                <p className="text-sm text-muted-foreground">Profit Factor</p>
                <p className="text-xl font-medium">
                  {metrics['Profit Factor'] ? Number(metrics['Profit Factor']).toFixed(2) : 'N/A'}
                </p>
              </div>
            </div>
            
            <div className="pt-4 mt-4 border-t">
              <h4 className="text-sm font-medium mb-2">Extracted Parameters</h4>
              <div className="flex flex-wrap gap-2">
                {Object.entries(parameters).map(([key, val]) => (
                  <span key={key} className="inline-flex items-center rounded-md bg-muted px-2 py-1 text-xs font-medium ring-1 ring-inset ring-muted-foreground/20">
                    {key}: {String(val)}
                  </span>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card className="border-primary/50 bg-primary/5">
            <CardHeader>
              <div className="flex items-center gap-2">
                <Lightbulb className="h-5 w-5 text-yellow-500" />
                <CardTitle>AI Optimization Suggestions</CardTitle>
              </div>
              <CardDescription>Grid search results over the historical dataset</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {optimization && optimization.parameter ? (
                <>
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-sm text-muted-foreground">Optimized Params</span>
                    <span className="font-medium">{optimization.parameter}</span>
                  </div>
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-sm text-muted-foreground">Tested Ranges</span>
                    <span className="font-medium text-sm text-right max-w-[200px]">{optimization.tested_ranges}</span>
                  </div>
                  <div className="flex justify-between border-b pb-2">
                    <span className="text-sm text-muted-foreground">Best Combination Found</span>
                    <span className="font-bold text-primary">{optimization.best_found}</span>
                  </div>
                  <div className="flex justify-between pt-1">
                    <span className="text-sm text-muted-foreground">Theoretical Improvement</span>
                    <span className="font-bold text-green-600">{optimization.improvement}</span>
                  </div>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">No optimizable parameters detected for this strategy.</p>
              )}
            </CardContent>
          </Card>

          {tearsheet_url ? (
            <Alert className="bg-muted">
              <FileSpreadsheet className="h-4 w-4" />
              <AlertTitle>Interactive Tearsheet Ready</AlertTitle>
              <AlertDescription className="mt-2 flex flex-col gap-3">
                <p>OpenStatz has generated a full interactive HTML report for this run.</p>
                <Button 
                  onClick={() => window.open(tearsheet_url, '_blank')}
                  className="w-full sm:w-auto"
                >
                  <ExternalLink className="mr-2 h-4 w-4" />
                  View OpenStatz Tearsheet
                </Button>
              </AlertDescription>
            </Alert>
          ) : (
             <Alert variant="destructive">
               <AlertTitle>Tearsheet Generation Failed</AlertTitle>
               <AlertDescription>
                 OpenStatz module was not found or failed to generate the HTML dashboard.
               </AlertDescription>
             </Alert>
          )}
        </div>
      </div>
    </div>
  )
}
