/** A key on the keyboard, in a hint: "Ctrl", "Enter", "K". */
export function Kbd({ children }: { children: string }) {
  return (
    <kbd className="rounded border border-input bg-muted px-1 font-mono text-xs">{children}</kbd>
  )
}
