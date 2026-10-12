import type { TestInfo } from '@playwright/test'

/** The viewport a test runs at (`desktop`, `phone`, …): the last part of a
 *  `playwright.ci.config.ts` project name (`customize/phone`), or the whole name. */
export const viewport = (info: TestInfo) => info.project.name.split('/').at(-1)!
