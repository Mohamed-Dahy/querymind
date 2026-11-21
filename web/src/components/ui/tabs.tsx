import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { LayoutGroup, m } from 'motion/react'
import { cn } from '@/lib/utils'
import { spring } from '@/motion/tokens'
import { Tabs as TabsPrimitive } from 'radix-ui'

/*
 * shadcn's tabs (Radix), restyled for Signal Lab (ADR 026): the active tab is marked by an indicator
 * that slides from the old tab to the new one. Motion's `layoutId` does the sliding: when an element
 * with the same layoutId appears somewhere else, motion animates it from the old place to the new one.
 * Radix still does everything else: roles, arrow keys, which panel is shown.
 */

/** Which tab is active, for the triggers (Radix keeps it to itself). */
const ActiveTab = React.createContext<string | undefined>(undefined)

function Tabs({
  className,
  orientation = 'horizontal',
  value,
  defaultValue,
  onValueChange,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Root>) {
  // Follow the active tab ourselves, whether the parent controls it (`value`) or not (`defaultValue`),
  // so each trigger knows whether it holds the indicator.
  const [uncontrolled, setUncontrolled] = React.useState(defaultValue)
  const active = value ?? uncontrolled
  // One LayoutGroup per tab set: two answers on the canvas each have their own indicator, and one
  // never flies over to the other.
  const group = React.useId()

  return (
    <ActiveTab.Provider value={active}>
      <LayoutGroup id={group}>
        <TabsPrimitive.Root
          data-slot="tabs"
          data-orientation={orientation}
          value={active}
          onValueChange={(next) => {
            setUncontrolled(next)
            onValueChange?.(next)
          }}
          className={cn('group/tabs flex gap-2 data-horizontal:flex-col', className)}
          {...props}
        />
      </LayoutGroup>
    </ActiveTab.Provider>
  )
}

const tabsListVariants = cva(
  'group/tabs-list inline-flex w-fit items-center justify-center rounded-lg p-[3px] text-muted-foreground group-data-horizontal/tabs:h-8 pointer-coarse:group-data-horizontal/tabs:h-12 group-data-vertical/tabs:h-fit group-data-vertical/tabs:flex-col data-[variant=line]:rounded-none',
  {
    variants: {
      variant: {
        default: 'border border-border bg-muted',
        line: 'gap-1 bg-transparent',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

function TabsList({
  className,
  variant = 'default',
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List> & VariantProps<typeof tabsListVariants>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      data-variant={variant}
      className={cn(tabsListVariants({ variant }), className)}
      {...props}
    />
  )
}

function TabsTrigger({
  className,
  children,
  value,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  const isActive = React.useContext(ActiveTab) === value

  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      value={value}
      className={cn(
        "relative inline-flex h-[calc(100%-1px)] flex-1 items-center justify-center gap-1.5 rounded-md border border-transparent px-2.5 py-0.5 text-sm font-medium whitespace-nowrap text-muted-foreground pointer-coarse:min-h-11 transition-colors duration-150 group-data-vertical/tabs:w-full group-data-vertical/tabs:justify-start hover:text-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring focus-visible:outline-1 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-50 has-data-[icon=inline-end]:pr-1 has-data-[icon=inline-start]:pl-1 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        'data-active:text-foreground',
        className,
      )}
      {...props}
    >
      {isActive && (
        <m.span
          aria-hidden="true"
          layoutId="tab-indicator"
          transition={spring.snappy}
          className={cn(
            'pointer-events-none absolute',
            // Default list: a raised surface pill behind the label.
            'group-data-[variant=default]/tabs-list:inset-0 group-data-[variant=default]/tabs-list:rounded-md group-data-[variant=default]/tabs-list:border group-data-[variant=default]/tabs-list:border-input group-data-[variant=default]/tabs-list:bg-surface',
            // Line list: a lime bar under (or beside) the label.
            'group-data-[variant=line]/tabs-list:bg-signal group-data-horizontal/tabs:group-data-[variant=line]/tabs-list:inset-x-1 group-data-horizontal/tabs:group-data-[variant=line]/tabs-list:-bottom-[5px] group-data-horizontal/tabs:group-data-[variant=line]/tabs-list:h-0.5 group-data-vertical/tabs:group-data-[variant=line]/tabs-list:inset-y-1 group-data-vertical/tabs:group-data-[variant=line]/tabs-list:-right-1 group-data-vertical/tabs:group-data-[variant=line]/tabs-list:w-0.5',
          )}
        />
      )}
      {/* Above the indicator, with the trigger's own icon-and-label layout. */}
      <span className="relative z-10 inline-flex items-center gap-1.5">{children}</span>
    </TabsPrimitive.Trigger>
  )
}

function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot="tabs-content"
      // No outline-none (shadcn's default): Radix makes a panel a Tab stop, so it needs the global
      // focus ring like every other stop (WCAG 2.4.7). Rounded, so the ring follows the cell's shape.
      className={cn('flex-1 rounded-lg text-sm', className)}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTrigger, TabsContent, tabsListVariants }
