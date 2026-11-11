/**
 * Node's garbage collector, callable from a test: a bug that only shows when the collector runs at a
 * bad moment (seen in CI only, 2026-11-11) can then be made to show every time.
 *
 * The V8 flag is set at run time and `gc` read in a fresh context, so no `--expose-gc` is needed.
 */
export async function exposeGc(): Promise<() => void> {
  // Specifiers in variables: the app's types don't include Node's, and these run only under Vitest.
  const v8Module = 'node:v8'
  const vmModule = 'node:vm'
  const v8 = (await import(/* @vite-ignore */ v8Module)) as {
    setFlagsFromString: (flags: string) => void
  }
  const vm = (await import(/* @vite-ignore */ vmModule)) as {
    runInNewContext: (code: string) => unknown
  }
  v8.setFlagsFromString('--expose-gc')
  return vm.runInNewContext('gc') as () => void
}

/** Collect a few times, letting pending callbacks run in between. */
export async function collectGarbage(): Promise<void> {
  const gc = await exposeGc()
  for (let round = 0; round < 5; round++) {
    gc()
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
}
