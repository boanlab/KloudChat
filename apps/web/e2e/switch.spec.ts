import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** The toggle's knob stays inside its track in both states. */
test('토글의 동그라미가 어느 상태에서도 트랙 밖으로 나가지 않는다', async ({ page }) => {
  await signIn(page)
  await page.goto('/agents')

  const toggle = page.getByRole('switch').first()
  await expect(toggle).toBeVisible({ timeout: 15_000 })
  const knob = toggle.locator('span')

  const measure = async () => {
    const track = await toggle.boundingBox()
    const dot = await knob.boundingBox()
    if (!track || !dot) throw new Error('토글을 측정하지 못했습니다')
    return {
      on: (await toggle.getAttribute('aria-checked')) === 'true',
      left: dot.x - track.x,
      right: track.x + track.width - (dot.x + dot.width),
    }
  }

  const first = await measure()
  await toggle.click()
  await page.waitForTimeout(400)
  const second = await measure()

  for (const state of [first, second]) {
    const where = state.on ? '켠' : '끈'
    expect(state.left, `${where} 상태에서 왼쪽으로 넘쳤습니다`).toBeGreaterThanOrEqual(0)
    expect(state.right, `${where} 상태에서 오른쪽으로 넘쳤습니다`).toBeGreaterThanOrEqual(0)
  }
  expect(first.on).not.toBe(second.on)

  // The two states mirror: the off-state right gap equals the on-state left gap.
  const off = first.on ? second : first
  const on = first.on ? first : second
  expect(Math.abs(off.left - on.right), '양 끝 여백이 어긋납니다').toBeLessThanOrEqual(1)

  // Left as found: other specs read this list.
  await toggle.click()
  await page.waitForTimeout(300)
})
