import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuthStore } from '@/stores/authStore'
import { useBrokerStore } from '@/stores/brokerStore'
import { useSessionStore } from '@/stores/sessionStore'
import { useThemeStore } from '@/stores/themeStore'
import { PageLoader } from '@/components/ui/page-loader'

interface AuthSyncProps {
  children: React.ReactNode
}

/**
 * AuthSync component that synchronizes Flask session with Zustand store.
 * This ensures the React app knows about authentication state from OAuth callbacks.
 * Also syncs app mode (live/analyzer) from the backend.
 */
export function AuthSync({ children }: AuthSyncProps) {
  const [isChecking, setIsChecking] = useState(true)
  const { setUser, setApiKey, logout, apiKey, isAuthenticated } = useAuthStore()
  const { fetchCapabilities, clearCapabilities } = useBrokerStore()
  const { setActiveSessionCount } = useSessionStore()
  const { syncAppMode } = useThemeStore()
  const hasSyncedRef = useRef(false)
  const hasApiKeyRetryRef = useRef(false)

  const syncSession = useCallback(async () => {
    try {
      const response = await fetch('/auth/session-status', {
        credentials: 'include',
      })

      if (response.ok) {
        const data = await response.json()

        if (data.status === 'success' && data.logged_in && data.broker) {
          // Flask session is authenticated with broker - sync to Zustand
          setUser({
            username: data.user,
            broker: data.broker,
            isLoggedIn: true,
            loginTime: new Date().toISOString(),
          })
          // Store the API key for trading API calls
          setApiKey(data.api_key || null)
          // Fetch broker capabilities (exchanges, type, features)
          await fetchCapabilities()
          // Also sync app mode from backend
          await syncAppMode()
          // Sync active session count
          if (data.active_sessions !== undefined) {
            setActiveSessionCount(data.active_sessions)
          }
        } else if (data.status === 'success' && data.authenticated && !data.logged_in) {
          // User is logged in but hasn't connected broker yet
          setUser({
            username: data.user,
            broker: null,
            isLoggedIn: false,
            loginTime: null,
          })
          setApiKey(null)
          clearCapabilities()
        } else {
          // Not authenticated or status is not success - clear Zustand store
          logout()
          clearCapabilities()
        }
      } else {
        // Any non-OK response (401, 500, etc.) - clear Zustand store
        logout()
        clearCapabilities()
      }
    } catch (_error) {
      // On error, don't change auth state - let existing state persist
    } finally {
      setIsChecking(false)
      hasSyncedRef.current = true
    }
  }, [setUser, setApiKey, logout, fetchCapabilities, clearCapabilities, syncAppMode, setActiveSessionCount])

  useEffect(() => {
    if (!hasSyncedRef.current) {
      syncSession()
    }
  }, [syncSession])

  useEffect(() => {
    if (hasSyncedRef.current && isAuthenticated && apiKey === null && !hasApiKeyRetryRef.current) {
      hasApiKeyRetryRef.current = true
      syncSession()
    }
  }, [isAuthenticated, apiKey, syncSession])

  // Show nothing while checking - prevents flash of wrong content
  if (isChecking) {
    return <PageLoader />
  }

  return <>{children}</>
}
