import {
  AlertTriangle,
  BarChart3,
  Brain,
  Calendar,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  FileCode,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  GripVertical,
  HelpCircle,
  Layers,
  MoreVertical,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Square,
  Trash2,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { pythonStrategyApi } from '@/api/python-strategy'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import type { MasterContractStatus, PythonStrategy } from '@/types/python-strategy'
import { SCHEDULE_DAYS, STATUS_COLORS, STATUS_LABELS } from '@/types/python-strategy'
import { showToast } from '@/utils/toast'

export default function PythonStrategyIndex() {
  const navigate = useNavigate()
  const [strategies, setStrategies] = useState<PythonStrategy[]>([])
  const [masterStatus, setMasterStatus] = useState<MasterContractStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [strategyToDelete, setStrategyToDelete] = useState<PythonStrategy | null>(null)
  const [currentTime, setCurrentTime] = useState(new Date())

  // Groups and Layout State
  const [customGroups, setCustomGroups] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem('openalgo_strategy_groups')
      return saved ? JSON.parse(saved) : []
    } catch {
      return []
    }
  })
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(() => {
    try {
      const saved = localStorage.getItem('openalgo_collapsed_groups')
      return saved ? JSON.parse(saved) : {}
    } catch {
      return {}
    }
  })

  // Drag-and-drop state
  const [draggedStrategyId, setDraggedStrategyId] = useState<string | null>(null)
  const [dragOverGroup, setDragOverGroup] = useState<string | null>(null)
  const [dragOverCardId, setDragOverCardId] = useState<string | null>(null)

  // Group Dialogs state
  const [createGroupDialogOpen, setCreateGroupDialogOpen] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [renameGroupDialogOpen, setRenameGroupDialogOpen] = useState(false)
  const [groupToRename, setGroupToRename] = useState<string | null>(null)
  const [renamedGroupName, setRenamedGroupName] = useState('')

  const fetchData = async (silent = false) => {
    try {
      if (!silent) setLoading(true)
      const [strategiesData, statusData] = await Promise.all([
        pythonStrategyApi.getStrategies(),
        pythonStrategyApi.getMasterContractStatus(),
      ])

      // Sort strategies by order index
      const sorted = [...strategiesData].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
      setStrategies(sorted)
      setMasterStatus(statusData)

      // Merge backend groups into customGroups if any exist
      const groupsFromBackend = Array.from(
        new Set(sorted.map((s) => s.group).filter(Boolean) as string[])
      )
      setCustomGroups((prev) => {
        const merged = Array.from(new Set([...prev, ...groupsFromBackend]))
        try {
          localStorage.setItem('openalgo_strategy_groups', JSON.stringify(merged))
        } catch (_e) {}
        return merged
      })
    } catch (_error) {
      if (!silent) showToast.error('Failed to load strategies', 'pythonStrategy')
    } finally {
      if (!silent) setLoading(false)
    }
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: mount-only init of the 1s timer and SSE subscription; adding fetchData would tear down and recreate the EventSource on every render
  useEffect(() => {
    fetchData()
    // Update current time every second
    const timer = setInterval(() => setCurrentTime(new Date()), 1000)

    // Subscribe to SSE for real-time status updates
    const eventSource = new EventSource('/python/api/events')

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'connected') {
          return
        }

        // Refresh data silently when we receive a status update
        if (data.strategy_id && data.status) {
          fetchData(true) // Silent refresh
        }
      } catch (_e) {
        // Ignore parse errors (heartbeat messages)
      }
    }

    eventSource.onerror = () => {}

    return () => {
      clearInterval(timer)
      eventSource.close()
    }
  }, [])

  // Layout persistence
  const persistLayout = async (newStrategies: PythonStrategy[], newGroups: string[]) => {
    const updated = newStrategies.map((s, idx) => ({ ...s, order: idx }))
    setStrategies(updated)

    try {
      localStorage.setItem('openalgo_strategy_groups', JSON.stringify(newGroups))
      localStorage.setItem('openalgo_strategy_order', JSON.stringify(updated.map((s) => s.id)))
    } catch (_e) {}

    try {
      const groupPayload = newGroups.map((grpName) => ({
        name: grpName,
        strategy_ids: updated.filter((s) => s.group === grpName).map((s) => s.id),
      }))

      await pythonStrategyApi.saveLayout({
        strategy_order: updated.map((s) => s.id),
        groups: groupPayload,
      })
    } catch (_e) {
      // Swallowed: local state is already updated optimistically
    }
  }

  // Group Management Handlers
  const handleCreateGroup = () => {
    const trimmed = newGroupName.trim()
    if (!trimmed) return
    if (customGroups.includes(trimmed)) {
      showToast.error(`Group "${trimmed}" already exists`, 'pythonStrategy')
      return
    }
    const updatedGroups = [...customGroups, trimmed]
    setCustomGroups(updatedGroups)
    setNewGroupName('')
    setCreateGroupDialogOpen(false)
    persistLayout(strategies, updatedGroups)
    showToast.success(`Group "${trimmed}" created`, 'pythonStrategy')
  }

  const handleRenameGroup = () => {
    if (!groupToRename) return
    const trimmed = renamedGroupName.trim()
    if (!trimmed || trimmed === groupToRename) {
      setRenameGroupDialogOpen(false)
      return
    }
    if (customGroups.includes(trimmed)) {
      showToast.error(`Group "${trimmed}" already exists`, 'pythonStrategy')
      return
    }
    const updatedGroups = customGroups.map((g) => (g === groupToRename ? trimmed : g))
    const updatedStrategies = strategies.map((s) =>
      s.group === groupToRename ? { ...s, group: trimmed } : s
    )
    setCustomGroups(updatedGroups)
    setRenameGroupDialogOpen(false)
    setGroupToRename(null)
    setRenamedGroupName('')
    persistLayout(updatedStrategies, updatedGroups)
    showToast.success(`Renamed to "${trimmed}"`, 'pythonStrategy')
  }

  const handleDeleteGroup = (groupName: string) => {
    const updatedGroups = customGroups.filter((g) => g !== groupName)
    const updatedStrategies = strategies.map((s) =>
      s.group === groupName ? { ...s, group: '' } : s
    )
    setCustomGroups(updatedGroups)
    persistLayout(updatedStrategies, updatedGroups)
    showToast.success(`Group "${groupName}" removed (strategies ungrouped)`, 'pythonStrategy')
  }

  const handleMoveToGroup = (strategyId: string, targetGroup: string) => {
    const updated = strategies.map((s) => (s.id === strategyId ? { ...s, group: targetGroup } : s))
    persistLayout(updated, customGroups)
    showToast.success(
      targetGroup ? `Moved to "${targetGroup}"` : 'Moved to Ungrouped',
      'pythonStrategy'
    )
  }

  const toggleGroupCollapse = (groupName: string) => {
    setCollapsedGroups((prev) => {
      const next = { ...prev, [groupName]: !prev[groupName] }
      try {
        localStorage.setItem('openalgo_collapsed_groups', JSON.stringify(next))
      } catch (_e) {}
      return next
    })
  }

  // Drag and Drop Handlers
  const handleDragStart = (e: React.DragEvent, id: string) => {
    e.dataTransfer.setData('text/plain', id)
    e.dataTransfer.effectAllowed = 'move'
    setDraggedStrategyId(id)
  }

  const handleDragEnd = () => {
    setDraggedStrategyId(null)
    setDragOverGroup(null)
    setDragOverCardId(null)
  }

  const handleDragOverGroup = (e: React.DragEvent, groupName: string) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (dragOverGroup !== groupName) {
      setDragOverGroup(groupName)
    }
  }

  const handleDropOnGroup = (e: React.DragEvent, targetGroup: string) => {
    e.preventDefault()
    e.stopPropagation()
    const id = e.dataTransfer.getData('text/plain') || draggedStrategyId
    if (!id) return

    const dragged = strategies.find((s) => s.id === id)
    if (!dragged) return

    const remaining = strategies.filter((s) => s.id !== id)
    const updatedDragged = { ...dragged, group: targetGroup }

    const lastIndex = remaining.map((s) => s.group).lastIndexOf(targetGroup)
    let newStrategies: PythonStrategy[]
    if (lastIndex === -1) {
      newStrategies = [...remaining, updatedDragged]
    } else {
      newStrategies = [
        ...remaining.slice(0, lastIndex + 1),
        updatedDragged,
        ...remaining.slice(lastIndex + 1),
      ]
    }

    persistLayout(newStrategies, customGroups)
    handleDragEnd()
  }

  const handleDragOverCard = (e: React.DragEvent, cardId: string) => {
    e.preventDefault()
    e.stopPropagation()
    e.dataTransfer.dropEffect = 'move'
    if (dragOverCardId !== cardId) {
      setDragOverCardId(cardId)
    }
  }

  const handleDropOnCard = (e: React.DragEvent, targetCardId: string, targetGroup: string) => {
    e.preventDefault()
    e.stopPropagation()
    const id = e.dataTransfer.getData('text/plain') || draggedStrategyId
    if (!id || id === targetCardId) {
      handleDragEnd()
      return
    }

    const dragged = strategies.find((s) => s.id === id)
    if (!dragged) return

    const updatedDragged = { ...dragged, group: targetGroup }
    const filtered = strategies.filter((s) => s.id !== id)
    const targetIdx = filtered.findIndex((s) => s.id === targetCardId)

    let newStrategies: PythonStrategy[]
    if (targetIdx === -1) {
      newStrategies = [...filtered, updatedDragged]
    } else {
      newStrategies = [
        ...filtered.slice(0, targetIdx),
        updatedDragged,
        ...filtered.slice(targetIdx),
      ]
    }

    persistLayout(newStrategies, customGroups)
    handleDragEnd()
  }

  const handleStart = async (strategy: PythonStrategy) => {
    try {
      setActionLoading(strategy.id)
      const response = await pythonStrategyApi.startStrategy(strategy.id)
      if (response.status === 'success') {
        showToast.success(response.message || `Strategy ${strategy.name} started`, 'pythonStrategy')
        fetchData()
      } else {
        showToast.error(response.message || 'Failed to start strategy', 'pythonStrategy')
      }
    } catch (error: unknown) {
      const axiosError = error as { response?: { data?: { message?: string } } }
      const errorMessage = axiosError.response?.data?.message || 'Failed to start strategy'
      showToast.error(errorMessage, 'pythonStrategy')
    } finally {
      setActionLoading(null)
    }
  }

  const handleStop = async (strategy: PythonStrategy) => {
    try {
      setActionLoading(strategy.id)
      const response = await pythonStrategyApi.stopStrategy(strategy.id)
      if (response.status === 'success') {
        showToast.success(response.message || `Strategy ${strategy.name} stopped`, 'pythonStrategy')
        fetchData()
      } else {
        showToast.error(response.message || 'Failed to stop strategy', 'pythonStrategy')
      }
    } catch (_error) {
      showToast.error('Failed to stop strategy', 'pythonStrategy')
    } finally {
      setActionLoading(null)
    }
  }

  const handleClearError = async (strategy: PythonStrategy) => {
    try {
      setActionLoading(strategy.id)
      const response = await pythonStrategyApi.clearError(strategy.id)
      if (response.status === 'success') {
        showToast.success('Error cleared', 'pythonStrategy')
        fetchData()
      } else {
        showToast.error(response.message || 'Failed to clear error', 'pythonStrategy')
      }
    } catch (_error) {
      showToast.error('Failed to clear error', 'pythonStrategy')
    } finally {
      setActionLoading(null)
    }
  }

  const handleDelete = async () => {
    if (!strategyToDelete) return
    try {
      setActionLoading(strategyToDelete.id)
      const response = await pythonStrategyApi.deleteStrategy(strategyToDelete.id)
      if (response.status === 'success') {
        showToast.success('Strategy deleted', 'pythonStrategy')
        setStrategies(strategies.filter((s) => s.id !== strategyToDelete.id))
      } else {
        showToast.error(response.message || 'Failed to delete strategy', 'pythonStrategy')
      }
    } catch (_error) {
      showToast.error('Failed to delete strategy', 'pythonStrategy')
    } finally {
      setActionLoading(null)
      setDeleteDialogOpen(false)
      setStrategyToDelete(null)
    }
  }

  const handleExport = async (strategy: PythonStrategy) => {
    try {
      const blob = await pythonStrategyApi.exportStrategy(strategy.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = strategy.file_name
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      showToast.success('Strategy exported', 'pythonStrategy')
    } catch (_error) {
      showToast.error('Failed to export strategy', 'pythonStrategy')
    }
  }

  const handleBacktest = (strategy: PythonStrategy) => {
    navigate('/tools/python-backtester', {
      state: { strategyId: strategy.id, symbol: strategy.exchange || 'NIFTY' },
    })
  }

  const handleCheckContracts = async () => {
    try {
      setActionLoading('master')
      const response = await pythonStrategyApi.checkAndStartPending()
      if (response.status === 'success') {
        const started = response.data?.started || 0
        showToast.success(`Started ${started} pending strategies`, 'pythonStrategy')
        fetchData()
      } else {
        showToast.error(response.message || 'Failed to check contracts', 'pythonStrategy')
      }
    } catch (_error) {
      showToast.error('Failed to check contracts', 'pythonStrategy')
    } finally {
      setActionLoading(null)
    }
  }

  const formatScheduleDays = (days: string[]) => {
    if (!days || days.length === 0) return ''
    if (days.length === 7) return 'Every day'
    if (days.length === 5 && !days.includes('sat') && !days.includes('sun')) return 'Weekdays'
    return days
      .map((d) => SCHEDULE_DAYS.find((sd) => sd.value === d)?.label.slice(0, 3) || d)
      .join(', ')
  }

  const formatTime = (timeStr: string | null) => {
    if (!timeStr) return '-'
    return new Date(timeStr).toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  // Stats
  const stats = {
    total: strategies.length,
    running: strategies.filter((s) => s.status === 'running').length,
    scheduled: strategies.filter((s) => s.is_scheduled).length,
  }

  if (loading) {
    return (
      <div className="container mx-auto py-6 space-y-6">
        <div className="flex justify-between items-center">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-10 w-32" />
        </div>
        <div className="grid gap-4 md:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-64" />
          ))}
        </div>
      </div>
    )
  }

  // Render a strategy card with drag handles and action menus
  const renderStrategyCard = (strategy: PythonStrategy, currentGroup: string) => {
    const isBeingDragged = draggedStrategyId === strategy.id
    const isTargetOver = dragOverCardId === strategy.id && !isBeingDragged

    return (
      <Card
        key={strategy.id}
        onDragOver={(e) => handleDragOverCard(e, strategy.id)}
        onDrop={(e) => handleDropOnCard(e, strategy.id, currentGroup)}
        className={cn(
          'relative overflow-hidden flex flex-col transition-all duration-150',
          isBeingDragged && 'opacity-40 scale-[0.98] border-dashed border-primary/50',
          isTargetOver && 'ring-2 ring-primary border-primary shadow-md'
        )}
      >
        {/* Status indicator bar */}
        <div className={`absolute top-0 left-0 right-0 h-1 ${STATUS_COLORS[strategy.status]}`} />

        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-2 overflow-hidden">
            {/* Grip handle & Title */}
            <div className="flex items-start gap-2 min-w-0 flex-1">
              <div
                draggable
                onDragStart={(e) => handleDragStart(e, strategy.id)}
                onDragEnd={handleDragEnd}
                className="cursor-grab active:cursor-grabbing p-1 -ml-1 mt-0.5 rounded text-muted-foreground/60 hover:text-foreground hover:bg-muted/70 transition-colors"
                title="Drag to reorder or move across groups"
              >
                <GripVertical className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1 space-y-1">
                <CardTitle className="text-lg truncate">{strategy.name}</CardTitle>
                <CardDescription className="font-mono text-xs truncate">
                  {strategy.file_name}
                </CardDescription>
              </div>
            </div>

            {/* Badges & Actions */}
            <div className="flex items-center gap-1 shrink-0">
              <Tooltip>
                <TooltipTrigger>
                  <Badge
                    variant={strategy.status === 'running' ? 'default' : 'secondary'}
                    className={`${STATUS_COLORS[strategy.status] || ''} whitespace-nowrap`}
                  >
                    {STATUS_LABELS[strategy.status] || strategy.status}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  {strategy.status_message || STATUS_LABELS[strategy.status]}
                </TooltipContent>
              </Tooltip>

              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" aria-label="Strategy actions menu">
                    <MoreVertical className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {/* Quick Move to Group submenu */}
                  <DropdownMenuSub>
                    <DropdownMenuSubTrigger>
                      <Folder className="h-4 w-4 mr-2" />
                      Move to Group
                    </DropdownMenuSubTrigger>
                    <DropdownMenuSubContent>
                      <DropdownMenuItem
                        onClick={() => handleMoveToGroup(strategy.id, '')}
                        className={cn(!strategy.group && 'font-semibold text-primary')}
                      >
                        Ungrouped {!strategy.group && '✓'}
                      </DropdownMenuItem>
                      {customGroups.map((grp) => (
                        <DropdownMenuItem
                          key={grp}
                          onClick={() => handleMoveToGroup(strategy.id, grp)}
                          className={cn(strategy.group === grp && 'font-semibold text-primary')}
                        >
                          {grp} {strategy.group === grp && '✓'}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuSubContent>
                  </DropdownMenuSub>

                  <DropdownMenuSeparator />

                  <DropdownMenuItem onClick={() => handleBacktest(strategy)}>
                    <BarChart3 className="h-4 w-4 mr-2" />
                    Run Backtest
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleExport(strategy)}>
                    <Download className="h-4 w-4 mr-2" />
                    Export
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    className="text-red-500"
                    disabled={strategy.status === 'running'}
                    onClick={() => {
                      setStrategyToDelete(strategy)
                      setDeleteDialogOpen(true)
                    }}
                  >
                    <Trash2 className="h-4 w-4 mr-2" />
                    Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </CardHeader>

        <CardContent className="space-y-4 flex-1 flex flex-col">
          {/* Schedule Info */}
          <div className="text-sm p-2 rounded min-h-[52px] bg-blue-500/10 border border-blue-500/20">
            <div className="flex items-center gap-2">
              <Calendar className="h-4 w-4 text-blue-500" />
              <span>
                {strategy.schedule_start_time || '09:00'} - {strategy.schedule_stop_time || '15:30'}{' '}
                IST
              </span>
            </div>
            <div className="text-xs text-muted-foreground mt-1 ml-6">
              {formatScheduleDays(strategy.schedule_days) || 'Every day'}
              {strategy.exchange && (
                <span className="ml-2 font-mono font-medium text-foreground">
                  ({strategy.exchange})
                </span>
              )}
            </div>
          </div>

          {/* Timestamps */}
          <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
            <div>
              <span className="block font-medium">Last Started:</span>
              <span>{formatTime(strategy.last_started)}</span>
            </div>
            <div>
              <span className="block font-medium">Last Stopped:</span>
              <span>{formatTime(strategy.last_stopped)}</span>
            </div>
          </div>

          {/* Error Message */}
          {strategy.error_message && (
            <Alert variant="destructive" className="py-2">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription className="text-xs flex items-center justify-between">
                <span className="truncate">{strategy.error_message}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs"
                  onClick={() => handleClearError(strategy)}
                  disabled={actionLoading === strategy.id}
                >
                  Clear
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {/* Card Action Buttons */}
          <div className="flex gap-2 pt-2 mt-auto">
            {strategy.status === 'running' ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="destructive"
                    size="sm"
                    className="flex-1"
                    onClick={() => handleStop(strategy)}
                    disabled={actionLoading === strategy.id}
                  >
                    <Square className="h-4 w-4 mr-2" />
                    Stop
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Stop running strategy</TooltipContent>
              </Tooltip>
            ) : strategy.is_scheduled && !strategy.manually_stopped ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1 border-amber-500 text-amber-600 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-950"
                    onClick={() => handleStop(strategy)}
                    disabled={actionLoading === strategy.id}
                  >
                    <Square className="h-4 w-4 mr-2" />
                    Disarm
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Pause scheduled runs</TooltipContent>
              </Tooltip>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="default"
                    size="sm"
                    className="flex-1 bg-green-600 hover:bg-green-700"
                    onClick={() => handleStart(strategy)}
                    disabled={actionLoading === strategy.id}
                  >
                    <Play className="h-4 w-4 mr-2" />
                    Start
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Start strategy</TooltipContent>
              </Tooltip>
            )}

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-blue-500 text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-950"
                  asChild
                  disabled={strategy.status === 'running'}
                >
                  <Link to={`/python/${strategy.id}/schedule`}>
                    <Pencil className="h-4 w-4 mr-1" />
                    <span className="text-xs">Schedule</span>
                  </Link>
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {strategy.schedule_start_time && strategy.schedule_stop_time
                  ? `${strategy.schedule_start_time} - ${strategy.schedule_stop_time}`
                  : 'Edit schedule'}
              </TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-emerald-500/50 text-emerald-600 hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-950"
                  onClick={() => handleBacktest(strategy)}
                >
                  <BarChart3 className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Run Backtest</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="outline" size="sm" asChild>
                  <Link to={`/python/${strategy.id}/logs`}>
                    <FileText className="h-4 w-4" />
                  </Link>
                </Button>
              </TooltipTrigger>
              <TooltipContent>View logs</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="outline" size="sm" asChild>
                  <Link to={`/python/${strategy.id}/edit`}>
                    <FileCode className="h-4 w-4" />
                  </Link>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Edit code</TooltipContent>
            </Tooltip>
          </div>
        </CardContent>
      </Card>
    )
  }

  // Organize strategies by groups
  const ungroupedStrategies = strategies.filter((s) => !s.group || !customGroups.includes(s.group))

  return (
    <div className="container mx-auto py-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Python Strategies</h1>
          <p className="text-muted-foreground">Manage and run your Python trading scripts</p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <Button variant="outline" size="sm" onClick={() => navigate('/strategy-analytics')}>
            <BarChart3 className="h-4 w-4 mr-2 text-indigo-500" />
            P&L Analytics
          </Button>
          <Button variant="outline" size="sm" onClick={() => navigate('/reinforcement-analytics')}>
            <Brain className="h-4 w-4 mr-2 text-purple-400" />
            RL Analytics
          </Button>
          <Button variant="outline" size="sm" onClick={() => navigate('/python/guide')}>
            <HelpCircle className="h-4 w-4 mr-2" />
            Guide
          </Button>
          <Button variant="outline" size="sm" onClick={() => fetchData()}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Button onClick={() => navigate('/python/new')}>
            <Plus className="h-4 w-4 mr-2" />
            Add Strategy
          </Button>
        </div>
      </div>

      {/* Stats Bar */}
      <div className="grid gap-4 grid-cols-2 md:grid-cols-4">
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Total</p>
                <p className="text-2xl font-bold">{stats.total}</p>
              </div>
              <FileCode className="h-8 w-8 text-muted-foreground" />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Running</p>
                <p className="text-2xl font-bold text-green-500">{stats.running}</p>
              </div>
              <Play className="h-8 w-8 text-green-500" />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Scheduled</p>
                <p className="text-2xl font-bold text-blue-500">{stats.scheduled}</p>
              </div>
              <Calendar className="h-8 w-8 text-blue-500" />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Master Contract</p>
                <Badge variant={masterStatus?.ready ? 'default' : 'secondary'}>
                  {masterStatus?.ready ? 'Ready' : 'Not Ready'}
                </Badge>
              </div>
              {!masterStatus?.ready && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleCheckContracts}
                  disabled={actionLoading === 'master'}
                >
                  Check
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Group Controls Bar */}
      <div className="flex items-center justify-between flex-wrap gap-3 py-1">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Clock className="h-4 w-4" />
          Current IST: {currentTime.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })}
        </div>
        <div className="flex items-center gap-2">
          {customGroups.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="h-8 text-xs text-muted-foreground hover:text-foreground"
              onClick={() => {
                const allCollapsed = customGroups.every((g) => collapsedGroups[g])
                const updated: Record<string, boolean> = {}
                for (const g of customGroups) {
                  updated[g] = !allCollapsed
                }
                setCollapsedGroups(updated)
                try {
                  localStorage.setItem('openalgo_collapsed_groups', JSON.stringify(updated))
                } catch (_e) {}
              }}
            >
              <Layers className="h-3.5 w-3.5 mr-1.5" />
              {customGroups.every((g) => collapsedGroups[g]) ? 'Expand All' : 'Collapse All'}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs border-dashed hover:border-solid shadow-xs"
            onClick={() => {
              setNewGroupName('')
              setCreateGroupDialogOpen(true)
            }}
          >
            <FolderPlus className="h-3.5 w-3.5 mr-1.5 text-primary" />
            New Group
          </Button>
        </div>
      </div>

      {/* Strategies Display */}
      {strategies.length === 0 ? (
        <Card className="py-12">
          <CardContent className="flex flex-col items-center justify-center text-center">
            <FileCode className="h-12 w-12 text-muted-foreground mb-4" />
            <h3 className="text-lg font-semibold mb-2">No Python Strategies</h3>
            <p className="text-muted-foreground mb-4">
              Upload your first Python trading script to get started.
            </p>
            <Button onClick={() => navigate('/python/new')}>
              <Plus className="h-4 w-4 mr-2" />
              Add Strategy
            </Button>
          </CardContent>
        </Card>
      ) : customGroups.length === 0 ? (
        /* Flat Grid with drag-and-drop support when no groups are defined */
        <div
          onDragOver={(e) => handleDragOverGroup(e, '')}
          onDrop={(e) => handleDropOnGroup(e, '')}
          className={cn(
            'grid gap-4 md:grid-cols-2 lg:grid-cols-3 items-start p-1 rounded-xl transition-all',
            dragOverGroup === '' && 'ring-2 ring-primary/30 rounded-xl bg-primary/5'
          )}
        >
          {strategies.map((strategy) => renderStrategyCard(strategy, ''))}
        </div>
      ) : (
        /* Grouped Sections with Drag and Drop */
        <div className="space-y-6">
          {customGroups.map((groupName) => {
            const groupStrategies = strategies.filter((s) => s.group === groupName)
            const isCollapsed = collapsedGroups[groupName]
            const runningInGroup = groupStrategies.filter((s) => s.status === 'running').length
            const isOverThisGroup = dragOverGroup === groupName

            return (
              <div
                key={groupName}
                onDragOver={(e) => handleDragOverGroup(e, groupName)}
                onDrop={(e) => handleDropOnGroup(e, groupName)}
                className={cn(
                  'rounded-xl border bg-card/40 transition-all duration-200 p-4 space-y-4 shadow-xs',
                  isOverThisGroup && 'border-primary/70 bg-primary/5 ring-2 ring-primary/20'
                )}
              >
                {/* Group Header */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 min-w-0">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground hover:text-foreground"
                      onClick={() => toggleGroupCollapse(groupName)}
                    >
                      {isCollapsed ? (
                        <ChevronRight className="h-4 w-4" />
                      ) : (
                        <ChevronDown className="h-4 w-4" />
                      )}
                    </Button>
                    <Folder className="h-4 w-4 text-primary shrink-0" />
                    <h2 className="font-semibold text-base truncate">{groupName}</h2>
                    <Badge variant="secondary" className="text-xs">
                      {groupStrategies.length}{' '}
                      {groupStrategies.length === 1 ? 'strategy' : 'strategies'}
                    </Badge>
                    {runningInGroup > 0 && (
                      <Badge className="bg-green-600/10 text-green-500 border-green-500/20 text-xs">
                        {runningInGroup} running
                      </Badge>
                    )}
                  </div>

                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-7 w-7">
                        <MoreVertical className="h-3.5 w-3.5" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        onClick={() => {
                          setGroupToRename(groupName)
                          setRenamedGroupName(groupName)
                          setRenameGroupDialogOpen(true)
                        }}
                      >
                        <Pencil className="h-3.5 w-3.5 mr-2" />
                        Rename Group
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        className="text-red-500"
                        onClick={() => handleDeleteGroup(groupName)}
                      >
                        <Trash2 className="h-3.5 w-3.5 mr-2" />
                        Delete Group
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>

                {/* Group Content */}
                {!isCollapsed &&
                  (groupStrategies.length === 0 ? (
                    <div
                      onDragOver={(e) => handleDragOverGroup(e, groupName)}
                      onDrop={(e) => handleDropOnGroup(e, groupName)}
                      className="border-2 border-dashed rounded-lg p-6 flex flex-col items-center justify-center text-center text-muted-foreground text-sm bg-muted/20"
                    >
                      <FolderOpen className="h-7 w-7 mb-2 opacity-50 text-muted-foreground" />
                      <p>Drag and drop strategies here to place them into "{groupName}"</p>
                    </div>
                  ) : (
                    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 items-start">
                      {groupStrategies.map((strategy) => renderStrategyCard(strategy, groupName))}
                    </div>
                  ))}
              </div>
            )
          })}

          {/* Ungrouped Strategies Section */}
          {ungroupedStrategies.length > 0 && (
            <div
              onDragOver={(e) => handleDragOverGroup(e, '')}
              onDrop={(e) => handleDropOnGroup(e, '')}
              className={cn(
                'rounded-xl border bg-muted/20 transition-all duration-200 p-4 space-y-4 shadow-xs',
                dragOverGroup === '' && 'border-primary/70 bg-primary/5 ring-2 ring-primary/20'
              )}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h2 className="font-semibold text-base text-muted-foreground">
                    Ungrouped Strategies
                  </h2>
                  <Badge variant="secondary" className="text-xs">
                    {ungroupedStrategies.length}{' '}
                    {ungroupedStrategies.length === 1 ? 'strategy' : 'strategies'}
                  </Badge>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 items-start">
                {ungroupedStrategies.map((strategy) => renderStrategyCard(strategy, ''))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Create Group Dialog */}
      <Dialog open={createGroupDialogOpen} onOpenChange={setCreateGroupDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Create Strategy Group</DialogTitle>
            <DialogDescription>
              Group your trading strategies by asset class, timeframe, or execution type.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <Input
              placeholder="e.g. Options Scalping, Commodities, Overnight"
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreateGroup()
              }}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateGroupDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreateGroup} disabled={!newGroupName.trim()}>
              Create Group
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Rename Group Dialog */}
      <Dialog open={renameGroupDialogOpen} onOpenChange={setRenameGroupDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename Strategy Group</DialogTitle>
            <DialogDescription>Enter a new name for this strategy group.</DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <Input
              value={renamedGroupName}
              onChange={(e) => setRenamedGroupName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleRenameGroup()
              }}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameGroupDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleRenameGroup} disabled={!renamedGroupName.trim()}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Strategy Dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Strategy</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{strategyToDelete?.name}"? This will remove the
              strategy file and all associated logs.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              Delete Strategy
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
