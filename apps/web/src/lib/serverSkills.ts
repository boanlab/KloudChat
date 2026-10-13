import type { Skill } from '@/types'

/** `process` keys whose work the server runs around a report or deck (`skill_specs.SERVER_PROCESS`). */
const SERVER_PROCESS = ['prepass', 'checks', 'stages', 'code_calc']

/** A skill whose tool work the server does on a report or deck: a pre-pass, a code check, the code calculation, or a check skill itself. */
export function runsOnServer(skill: Pick<Skill, 'server'>): boolean {
  const server = skill.server
  if (!server) return false
  const process = (server.process ?? {}) as Record<string, unknown>
  const set = (value: unknown) => (Array.isArray(value) ? value.length > 0 : Boolean(value))
  return Boolean(server.stage) || SERVER_PROCESS.some((key) => set(process[key]))
}
