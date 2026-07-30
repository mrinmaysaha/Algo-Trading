import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@/test/test-utils'
import { AuthSync } from './AuthSync'
import { useAuthStore } from '@/stores/authStore'

function resetAuthStore() {
  useAuthStore.setState({
    user: null,
    apiKey: null,
    isAuthenticated: false,
  })
  window.localStorage.removeItem('openalgo-auth')
}

function createFetchResponse(data: unknown) {
  return {
    ok: true,
    json: async () => data,
  }
}

describe('AuthSync', () => {
  beforeEach(() => {
    resetAuthStore()
    vi.restoreAllMocks()
  })

  it('syncs Flask session status into auth store and renders children', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input) => {
      const url =
        typeof input === 'string'
          ? input
          : input && typeof input === 'object' && 'url' in input
          ? (input as { url: string }).url
          : ''

      if (url.endsWith('/auth/session-status')) {
        return Promise.resolve(
          createFetchResponse({
            status: 'success',
            authenticated: true,
            logged_in: true,
            user: 'alice',
            broker: 'zerodha',
            api_key: 'test-api-key',
            active_sessions: 1,
          })
        )
      }

      if (url.endsWith('/api/broker/capabilities')) {
        return Promise.resolve(
          createFetchResponse({
            status: 'success',
            data: {
              broker_name: 'Zerodha',
              broker_type: 'IN_stock',
              supported_exchanges: ['NSE'],
              leverage_config: true,
            },
          })
        )
      }

      if (url.endsWith('/auth/analyzer-mode')) {
        return Promise.resolve(createFetchResponse({ status: 'success', data: { analyze_mode: false } }))
      }

      return Promise.resolve({ ok: false, json: async () => ({}) })
    }))

    const setUserSpy = vi.spyOn(useAuthStore.getState(), 'setUser')
    const setApiKeySpy = vi.spyOn(useAuthStore.getState(), 'setApiKey')

    render(
      <AuthSync>
        <div>session-ready</div>
      </AuthSync>
    )

    expect(screen.getByText(/loading/i)).toBeInTheDocument()

    await waitFor(() => expect(screen.getByText('session-ready')).toBeInTheDocument())

    expect(setUserSpy).toHaveBeenCalled()
    expect(setApiKeySpy).toHaveBeenCalled()

    expect(useAuthStore.getState()).toMatchObject({
      user: {
        username: 'alice',
        broker: 'zerodha',
        isLoggedIn: true,
      },
      apiKey: 'test-api-key',
      isAuthenticated: true,
    })
  })
})
