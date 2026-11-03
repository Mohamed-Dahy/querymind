import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'

import { HistoryRail } from './HistoryRail'
import type { DeleteWithUndo } from './useDeleteWithUndo'

type HistorySheetProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  deletes: DeleteWithUndo
  /** Radix only gives the focus back to its own trigger, and this one isn't. */
  opener: React.RefObject<HTMLElement | null>
}

/** The history on a phone, where the rail has no room. Its own file, so desktops never load it. */
export function HistorySheet({ open, onOpenChange, deletes, opener }: HistorySheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="left"
        className="overflow-y-auto"
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          opener.current?.focus()
        }}
      >
        <SheetHeader className="pb-0">
          <SheetTitle>History</SheetTitle>
          <SheetDescription>Your saved conversations.</SheetDescription>
        </SheetHeader>
        <div className="px-3 pb-6">
          <HistoryRail deletes={deletes} onOpen={() => onOpenChange(false)} />
        </div>
      </SheetContent>
    </Sheet>
  )
}
