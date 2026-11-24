/** Drops of untouched blank documents still on their way: a list asked for right after leaving waits for them. */
const running = new Set<Promise<unknown>>()

export function trackBlank<T>(job: Promise<T>): Promise<T> {
  running.add(job)
  const done = () => running.delete(job)
  job.then(done, done)
  return job
}

export function blankSettled(): Promise<void> {
  return running.size ? Promise.allSettled([...running]).then(() => undefined) : Promise.resolve()
}
