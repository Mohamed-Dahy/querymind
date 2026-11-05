import { useQuery } from '@tanstack/react-query'
import {
  Gauge,
  LayoutGrid,
  MessageSquare,
  Monitor,
  Moon,
  Network,
  SendHorizontal,
  SquarePen,
  Sun,
  Table2,
  BookOpen,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { useNavigate } from 'react-router'

import { ApiError } from '@/api/client'
import { CONVERSATIONS_KEY, listConversations } from '@/api/conversations'
import { MAX_QUESTION_LENGTH } from '@/canvas/question'
import {
  useHistoryActions,
  useNewConversation,
  useNewConversationBusy,
} from '@/canvas/useConversation'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { EVAL_VIEWS } from '@/evals/views'
import { businessLabel, termAnchor, termName } from '@/explorer/data'
import { useExplorerData, useSemanticOverview } from '@/explorer/useExplorerData'
import { useTheme, type ThemeChoice } from '@/lib/theme'

import {
  paletteGroups,
  paletteNotes,
  type GroupName,
  type PaletteAction,
  type PaletteItem,
  type PaletteSources,
  type Source,
} from './palette'

type CommandPaletteProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** What had focus when the palette opened: it gets focus back unless a choice moved the page. */
  opener: RefObject<HTMLElement | null>
  /** Conversations deleted a moment ago, still waiting out their Undo. */
  hidden: ReadonlySet<string>
}

// Enough to find a conversation by its title; the history rail has the full list and its search.
const CONVERSATION_LIMIT = 50

const GROUP_ICONS: Record<GroupName, LucideIcon> = {
  Actions: SquarePen,
  Pages: LayoutGrid,
  Conversations: MessageSquare,
  Tables: Table2,
  Terms: BookOpen,
  Ask: SendHorizontal,
}

const THEME_ICONS: Record<ThemeChoice, LucideIcon> = { dark: Moon, light: Sun, system: Monitor }

const PAGES: PaletteItem[] = [
  { id: 'page:/', label: 'Canvas', keywords: ['ask', 'home'], action: nav('/') },
  {
    id: 'page:/explore',
    label: 'Data explorer',
    keywords: ['schema', 'tables'],
    action: nav('/explore'),
  },
  {
    id: 'page:/explore/glossary',
    label: 'Business glossary',
    keywords: ['metrics', 'terms'],
    action: nav('/explore/glossary'),
  },
  ...EVAL_VIEWS.map(({ to, label }) => ({
    id: `page:${to}`,
    label,
    hint: 'Evals lab',
    keywords: ['evals', 'evaluation'],
    action: nav(to),
  })),
]

/** Pages and themes each get their own icon (the same as in the rail and the switcher). */
function itemIcon({ action }: PaletteItem, group: GroupName): LucideIcon | undefined {
  if (action.kind === 'theme') return THEME_ICONS[action.choice]
  if (group !== 'Pages' || action.kind !== 'navigate') return undefined
  if (action.to.startsWith('/evals')) return Gauge
  if (action.to === '/explore/glossary') return BookOpen
  if (action.to === '/explore') return Network
  return LayoutGrid
}

function nav(to: string): PaletteAction {
  return { kind: 'navigate', to }
}

/**
 * The ⌘K / Ctrl+K palette: one search box for every page, conversation, table and term, plus the
 * few actions (new conversation, theme, ask). Each item also has a normal visible path; this is a
 * shortcut, not the only way. Its own file, loaded the first time it opens.
 */
export default function CommandPalette({
  open,
  onOpenChange,
  opener,
  hidden,
}: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  // A choice that changed the page: focus then belongs to the new page, not to the old opener.
  const moved = useRef(false)
  const navigate = useNavigate()
  const newConversation = useNewConversation()
  const { askNew } = useHistoryActions()
  const busy = useNewConversationBusy()
  const { choice: theme, setChoice } = useTheme()
  const sources = usePaletteSources(open, hidden, theme)

  const groups = paletteGroups(query, sources, { busy, maxLength: MAX_QUESTION_LENGTH })
  const notes = paletteNotes(sources)
  const list = useRef<HTMLDivElement>(null)

  // A new search starts at the top, where its best match is (and is selected). cmdk only scrolls
  // when the selected item changes, so an old scroll position would otherwise hide it.
  useEffect(() => {
    if (list.current) list.current.scrollTop = 0
  }, [query])

  function choose({ action }: PaletteItem) {
    moved.current = action.kind !== 'theme'
    onOpenChange(false)
    setQuery('')
    if (action.kind === 'navigate') void navigate(action.to)
    else if (action.kind === 'new-conversation') newConversation()
    else if (action.kind === 'theme') setChoice(action.choice)
    else askNew(action.question)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setQuery('')
        onOpenChange(next)
      }}
    >
      <DialogContent
        className="top-[12dvh] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-xl"
        onOpenAutoFocus={() => {
          moved.current = false
        }}
        onCloseAutoFocus={(event) => {
          // Radix would focus its trigger, and the palette has none (a shortcut or the top-bar
          // button opens it). After a move, the new page has already put focus on <main>.
          event.preventDefault()
          const main = document.getElementById('main')
          if (!moved.current && opener.current?.isConnected) opener.current.focus()
          else if (document.activeElement === document.body) main?.focus()
        }}
      >
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <DialogDescription className="sr-only">
          Go to a page, a conversation, a table or a business term, or ask a new question. Arrow
          keys move, Enter chooses, Escape closes.
        </DialogDescription>
        {/* Our own filter (palette.ts), so the ranking and the Ask rule are ours and tested. */}
        <Command shouldFilter={false} label="Command palette" loop>
          <CommandInput
            value={query}
            onValueChange={setQuery}
            placeholder="Search, or ask a question…"
          />
          <CommandList ref={list}>
            <CommandEmpty>Type to search, or to ask a question.</CommandEmpty>
            {groups.map(({ name, items }) => {
              const Icon = GROUP_ICONS[name]
              return (
                <CommandGroup key={name} heading={name}>
                  {items.map((item) => {
                    const ItemIcon = itemIcon(item, name) ?? Icon
                    return (
                      <CommandItem
                        key={item.id}
                        value={item.id}
                        disabled={item.disabledReason !== undefined}
                        onSelect={() => choose(item)}
                      >
                        <ItemIcon aria-hidden="true" className="shrink-0 text-fg-muted" />
                        <span className="min-w-0 flex-1 truncate">{item.label}</span>
                        {(item.disabledReason ?? item.hint) && (
                          <span className="shrink-0 text-xs text-fg-muted">
                            {item.disabledReason ?? item.hint}
                          </span>
                        )}
                      </CommandItem>
                    )
                  })}
                </CommandGroup>
              )
            })}
          </CommandList>
          {notes.length > 0 && (
            // Outside the list: only options may be inside a listbox. Polite, so a failure is heard.
            <div role="status" className="space-y-0.5 border-t px-3 py-2 text-xs text-fg-muted">
              {notes.map((note) => (
                <p key={note}>{note}</p>
              ))}
            </div>
          )}
        </Command>
      </DialogContent>
    </Dialog>
  )
}

/** Every list the palette searches. They load the first time it opens, not with the page. */
function usePaletteSources(
  open: boolean,
  hidden: ReadonlySet<string>,
  theme: ThemeChoice,
): PaletteSources {
  const conversations = useQuery({
    queryKey: [...CONVERSATIONS_KEY, 'palette'],
    queryFn: ({ signal }) => listConversations({ limit: CONVERSATION_LIMIT }, signal),
    enabled: open,
  })
  const explorer = useExplorerData()
  const semantic = useSemanticOverview()

  const conversationItems = conversations.data?.conversations
  const tables = explorer.data?.tables
  const businesses = semantic.data?.businesses

  return useMemo(() => {
    const actions: PaletteItem[] = [
      {
        id: 'action:new',
        label: 'New conversation',
        keywords: ['start over', 'clear'],
        action: { kind: 'new-conversation' },
      },
      ...(['dark', 'light', 'system'] as const).map((choice): PaletteItem => ({
        id: `theme:${choice}`,
        label: `${choice.charAt(0).toUpperCase()}${choice.slice(1)} theme`,
        ...(choice === theme && { hint: 'Current' }),
        keywords: ['theme', 'appearance', 'mode'],
        action: { kind: 'theme', choice },
      })),
    ]

    const conversationSource: Source = conversationItems
      ? {
          items: conversationItems
            .filter(({ id }) => !hidden.has(id))
            .map(({ id, title, pinned, updated_at: updated }) => ({
              id: `conversation:${id}`,
              label: title,
              hint: pinned
                ? 'Pinned'
                : new Date(updated).toLocaleDateString('en', { day: 'numeric', month: 'short' }),
              action: nav(`/c/${id}`),
            })),
        }
      : {
          items: [],
          note: conversations.isError
            ? conversations.error instanceof ApiError &&
              conversations.error.code === 'history_unavailable'
              ? "History isn't available on this server, so no conversations are listed."
              : "Conversations couldn't be loaded."
            : 'Loading conversations…',
        }

    const tableSource: Source = tables
      ? {
          items: tables.map(({ name, business, synonyms, columns }) => ({
            id: `table:${name}`,
            label: name,
            hint: businessLabel(business),
            keywords: [...synonyms, ...columns],
            action: nav(`/explore/tables/${encodeURIComponent(name)}`),
          })),
        }
      : {
          items: [],
          note: explorer.error ? "Tables can't be listed right now." : 'Loading tables…',
        }

    const termSource: Source = businesses
      ? {
          items: businesses.flatMap(({ database, metrics }) =>
            metrics.map(({ name, synonyms }) => ({
              id: `term:${name}`,
              label: termName(name),
              hint: businessLabel(database),
              keywords: [name, ...synonyms],
              action: nav(`/explore/glossary#${termAnchor(name)}`),
            })),
          ),
        }
      : {
          items: [],
          note: semantic.isError ? "Business terms can't be listed right now." : 'Loading terms…',
        }

    return {
      actions,
      pages: PAGES,
      conversations: conversationSource,
      tables: tableSource,
      terms: termSource,
    }
  }, [
    theme,
    conversationItems,
    hidden,
    conversations.isError,
    conversations.error,
    tables,
    explorer.error,
    businesses,
    semantic.isError,
  ])
}
