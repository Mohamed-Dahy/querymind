/** A key on the keyboard, in a hint: "Ctrl", "Enter", "K". A small keycap: a thicker bottom edge. */
export function Kbd({ children }: { children: string }) {
  return (
    <kbd className="rounded-[5px] border border-b-2 border-input bg-muted px-1.5 font-mono text-xs text-fg-muted">
      {children}
    </kbd>
  )
}
