import { NavLink, Outlet } from 'react-router'

import { useSession } from '@/api/session'
import { cn } from '@/lib/utils'

import { tabsFor } from './tabs'

/**
 * The Settings page (U7.1b): one heading, a side list of tabs, and the open tab beside it. Each tab
 * is its own URL (/settings/key …), so the list is links, not ARIA tabs: a link can be bookmarked,
 * opened in a new tab, and reached with Back. On a phone the list is a row that scrolls sideways.
 */
export default function SettingsLayout() {
  const session = useSession()
  const tabs = tabsFor(session?.user.role)

  return (
    <div className="mx-auto max-w-5xl px-4 py-10">
      <h1 className="text-2xl">Settings</h1>
      {session && (
        <p className="mt-2 text-fg-muted">
          For the workspace{' '}
          <span className="font-medium text-fg">{session.user.workspace.name}</span>.
        </p>
      )}

      <div className="mt-6 flex flex-col gap-6 md:flex-row">
        <nav aria-label="Settings" className="min-w-0 md:w-48 md:shrink-0">
          <ul className="flex gap-1 overflow-x-auto md:flex-col">
            {tabs.map(({ path, label, Icon }) => (
              <li key={path} className="shrink-0">
                {/* NavLink sets aria-current="page" on the open tab; the look follows it (weight
                    and background, not colour alone). */}
                <NavLink
                  to={`/settings/${path}`}
                  className={({ isActive }) =>
                    cn(
                      'flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm whitespace-nowrap transition-colors',
                      isActive
                        ? 'bg-muted font-medium text-fg'
                        : 'text-fg-muted hover:bg-muted hover:text-fg',
                    )
                  }
                >
                  <Icon aria-hidden="true" className="size-4" />
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="min-w-0 flex-1">
          <Outlet />
        </div>
      </div>
    </div>
  )
}
