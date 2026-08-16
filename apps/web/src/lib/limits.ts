/**
 * Lengths the API enforces, repeated here so a form can decline before the
 * round trip rather than after it.
 *
 * Kept in one file because the alternative is the number appearing in six
 * components and drifting from the schema in four of them. If the server's
 * cap moves, this is the one line to follow it.
 */

/** `name` on skills, agents, memories and projects. */
export const NAME_LIMIT = 120
