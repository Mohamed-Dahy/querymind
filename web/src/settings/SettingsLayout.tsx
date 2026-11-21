import { LayoutGroup, m } from 'motion/react'
import { NavLink, Outlet } from 'react-router'

import { useSession } from '@/api/session'
import { cn } from '@/lib/utils'
import { spring, useMotionSafe } from '@/motion'

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
      {/* Signal Lab (U8.7): a mono label over the title, as on the other pages. */}
      <p aria-hidden="true" className="label-mono">
        WORKSPACE · CONTROL ROOM
      </p>
      <h1 className="mt-1 text-3xl font-semibold tracking-tight">Settings</h1>
      {session && (
        <p className="mt-2 text-fg-muted">
          For the workspace{' '}
          <span className="font-medium text-fg">{session.user.workspace.name}</span>.
        </p>
      )}

      <div className="mt-6 flex flex-col gap-6 md:flex-row">
        <nav aria-label="Settings" className="min-w-0 md:w-48 md:shrink-0">
          {/* One lime line marks the open tab and slides to the next one (U8.7): under it on a
              phone, where the tabs are a row; on its left from 768 px, where they are a column. */}
          <LayoutGroup id="settings-tabs">
            <ul className="flex gap-1 overflow-x-auto md:flex-col">
              {tabs.map(({ path, label, Icon }) => (
                <li key={path} className="shrink-0">
                  {/* NavLink sets aria-current="page" on the open tab; the look follows it (weight
                    and background, not colour alone). */}
                  <NavLink
                    to={`/settings/${path}`}
                    className={({ isActive }) =>
                      cn(
                        'relative flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm whitespace-nowrap transition-colors duration-150',
                        isActive
                          ? 'bg-muted font-medium text-fg'
                          : 'text-fg-muted hover:bg-muted hover:text-fg',
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <Icon
                          aria-hidden="true"
                          className={cn('size-4', isActive && 'text-signal')}
                        />
                        {label}
                        {isActive && <TabIndicator />}
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </LayoutGroup>
        </nav>
        <div className="min-w-0 flex-1">
          <Outlet />
        </div>
      </div>
    </div>
  )
}

/**
 * The lime line on the open tab. With motion on it is one shared element (`layoutId`) that slides
 * from the old tab to the new one; under reduced motion it simply sits on the new one. Beside the
 * text, never behind it, so the text's contrast is measured on the tab's own colour.
 */
function TabIndicator() {
  const { animate } = useMotionSafe()
  const line =
    'absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-signal md:inset-x-auto md:inset-y-2 md:left-0 md:h-auto md:w-0.5'
  if (!animate) return <span aria-hidden="true" className={line} />
  return (
    <m.span
      aria-hidden="true"
      layoutId="settings-tab-indicator"
      transition={spring.snappy}
      className={line}
    />
  )
}
