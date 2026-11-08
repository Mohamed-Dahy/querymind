import type { Me, Session } from '@/api/session'

/** Who the fake API logs in (U7.1). Hand-made: the real API's /auth/me shape, with made-up ids. */
export const mockUser: Me = {
  id: '5f0d6c1e-0000-4000-8000-000000000001',
  email: 'analyst@example.com',
  role: 'admin',
  workspace: { id: '5f0d6c1e-0000-4000-8000-000000000002', name: "analyst's workspace" },
}

/** The session tests start with (test/setup.ts), so every page renders logged in at once. */
export const mockSession: Session = { accessToken: 'mock-access-token', user: mockUser }

/**
 * Mock mode and tests only: set after "Log out", so a reload stays logged out, as a cleared cookie
 * would. Logging in clears it. An e2e test sets it before the page loads to start logged out.
 */
export const MOCK_LOGGED_OUT_KEY = 'querymind-mock-logged-out'

export function mockLoggedOut(): boolean {
  try {
    return localStorage.getItem(MOCK_LOGGED_OUT_KEY) === '1'
  } catch {
    return false
  }
}

export function setMockLoggedOut(out: boolean) {
  try {
    if (out) localStorage.setItem(MOCK_LOGGED_OUT_KEY, '1')
    else localStorage.removeItem(MOCK_LOGGED_OUT_KEY)
  } catch {
    // No storage (a private window): the fake login just won't survive a reload.
  }
}
