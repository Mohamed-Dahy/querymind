import { Command as CommandPrimitive } from 'cmdk'
import { SearchIcon } from 'lucide-react'
import * as React from 'react'

import { cn } from '@/lib/utils'

// shadcn's Command, written by hand on cmdk so it uses our own Dialog (the shadcn CLI would have
// replaced dialog.tsx, which is restyled to the tokens). cmdk gives the search box and list the
// combobox roles, arrow keys, Enter and the "selected" item; we style them.

function Command({ className, ...props }: React.ComponentProps<typeof CommandPrimitive>) {
  return (
    <CommandPrimitive
      data-slot="command"
      className={cn('flex size-full flex-col overflow-hidden rounded-xl bg-surface', className)}
      {...props}
    />
  )
}

function CommandInput({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Input>) {
  return (
    <div data-slot="command-input-wrapper" className="flex items-center gap-2 border-b pr-12 pl-3">
      <SearchIcon aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
      <CommandPrimitive.Input
        data-slot="command-input"
        className={cn(
          'h-12 w-full bg-transparent text-base outline-none placeholder:text-fg-muted',
          className,
        )}
        {...props}
      />
    </div>
  )
}

function CommandList({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.List>) {
  return (
    <CommandPrimitive.List
      data-slot="command-list"
      className={cn('max-h-[min(60dvh,26rem)] scroll-py-1 overflow-y-auto p-1', className)}
      {...props}
    />
  )
}

function CommandEmpty(props: React.ComponentProps<typeof CommandPrimitive.Empty>) {
  return (
    <CommandPrimitive.Empty
      data-slot="command-empty"
      className="py-6 text-center text-sm text-fg-muted"
      {...props}
    />
  )
}

function CommandGroup({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Group>) {
  return (
    <CommandPrimitive.Group
      data-slot="command-group"
      className={cn(
        'p-1 **:[[cmdk-group-heading]]:px-2 **:[[cmdk-group-heading]]:py-1.5 **:[[cmdk-group-heading]]:text-xs **:[[cmdk-group-heading]]:text-fg-muted',
        className,
      )}
      {...props}
    />
  )
}

function CommandItem({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(
        // The selected item: background and an outline, so it shows without colour alone.
        'flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-2 text-sm outline-none select-none',
        'data-[selected=true]:bg-muted data-[selected=true]:ring-1 data-[selected=true]:ring-input',
        "data-[disabled=true]:cursor-not-allowed data-[disabled=true]:text-fg-muted [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      {...props}
    />
  )
}

export { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList }
