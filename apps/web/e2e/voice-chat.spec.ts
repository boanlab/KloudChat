import { expect, test, type Page } from '@playwright/test'
import { composerFixture } from './composer-fixture'

/** Voice conversation: speech becomes a turn, the answer is read back sentence by sentence. */

const answer = [
  '네, 정리해 드릴게요. 신청은 10월부터입니다.[1]',
  '',
  '| 항목 | 내용 |',
  '| --- | --- |',
  '| 규모 | 2억 원 |',
  '',
  '```python',
  'print("읽지 않는 코드")',
  '```',
  '',
  '이상입니다. 더 궁금한 점이 있으면 말씀하세요.',
].join('\n')

const spoken = '올해 지원 사업을 알려 줘.'

const model = (id: string, kinds: string[], extra: Record<string, unknown> = {}) => ({
  id, label: id, name: id, vendor: 'Fixture', provider: 'fixture', kinds, modality: 'chat',
  dataBoundary: 'external', strictLocal: false, privacyOnly: false, creditCost: 1, inputCreditCost: 1,
  supportsTools: false, description: '', ...extra,
})

const qwen = model('strict-local/qwen3-tts', ['av'], {
  modality: 'audio', dataBoundary: 'self_hosted', strictLocal: true, speechEndpoint: true,
  voices: ['sohee', 'vivian'], creditCost: 0, creditPerCall: 0,
})

/** A silent 0.3 s clip in the speech server's format. */
function wav(): Buffer {
  const rate = 24_000
  const samples = Math.floor(rate * 0.3)
  const buffer = Buffer.alloc(44 + samples * 2)
  buffer.write('RIFF', 0)
  buffer.writeUInt32LE(36 + samples * 2, 4)
  buffer.write('WAVEfmt ', 8)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(rate, 24)
  buffer.writeUInt32LE(rate * 2, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36)
  buffer.writeUInt32LE(samples * 2, 40)
  return buffer
}

/** A microphone that is silent until `window.__talk(ms)` makes it a tone. */
async function fakeMicrophone(page: Page) {
  await page.addInitScript(() => {
    const context = new AudioContext()
    const destination = context.createMediaStreamDestination()
    const gain = context.createGain()
    gain.gain.value = 0
    const tone = context.createOscillator()
    tone.frequency.value = 220
    tone.connect(gain).connect(destination)
    tone.start()
    navigator.mediaDevices.getUserMedia = async () => {
      await context.resume()
      return destination.stream
    }
    ;(window as unknown as { __talk: (ms: number) => void }).__talk = (ms) => {
      gain.gain.value = 0.6
      setTimeout(() => {
        gain.gain.value = 0
      }, ms)
    }
  })
}

async function stub(page: Page, { withVoice, silent = false }: { withVoice: boolean; silent?: boolean }) {
  const transcriptions: number[] = []
  /** Whether each request carried a `prompt`: an earlier answer as the hint emptied Whisper's reply. */
  const prompted: boolean[] = []
  const speaks: { text: string; voice: string; warm: boolean }[] = []
  await page.route('**/api/auth/config', (route) =>
    route.fulfill({ json: {
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report', 'slides', 'image', 'av'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: true,
    } }),
  )
  await page.route('**/api/models', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({ json: {
      litellmAvailable: true, defaultChatModel: 'fixture/model',
      defaults: { chat: 'fixture/model' }, defaultModelByKind: {},
      defaultAvModelByMode: withVoice ? { audio: qwen.id } : {},
      models: [model('fixture/model', ['chat', 'report', 'slides']), ...(withVoice ? [qwen] : [])],
      autoRouting: { enabled: false, available: false, reason: null, economyModelIds: [] },
    } })
  })
  await page.route('**/api/transcriptions', (route) => {
    transcriptions.push(Date.now())
    prompted.push((route.request().postData() ?? '').includes('name="prompt"'))
    // Whisper heard sound and made no words of it: the API answers 422 with this sentence.
    if (silent) return route.fulfill({ status: 422, json: { detail: '들린 말이 없습니다.' } })
    return route.fulfill({ json: { text: spoken, seconds: 2 } })
  })
  await page.route('**/api/audio/speak', (route) => {
    speaks.push(route.request().postDataJSON() as { text: string; voice: string; warm: boolean })
    return route.fulfill({ status: 200, contentType: 'audio/wav', body: wav() })
  })
  return { transcriptions, prompted, speaks }
}

test('말하면 질문이 되고, 답은 문장마다 읽어 준다', async ({ page }, testInfo) => {
  await fakeMicrophone(page)
  const { speaks, transcriptions, prompted } = await stub(page, { withVoice: true })
  const { sends, unexpected } = await composerFixture(page, testInfo, {
    turn: () => ({ events: [
      { type: 'delta', text: answer },
      { type: 'usage', inputTokens: 4, outputTokens: 4, credits: 0 }, { type: 'done' },
    ] }),
  })
  await page.goto('/new/chat')
  await page.getByTestId('voice-chat-button').click()
  const panel = page.getByTestId('voice-panel')
  await expect(panel).toBeVisible()
  await expect(page.getByTestId('voice-status')).toHaveText('듣고 있습니다. 말씀하세요')

  await page.evaluate(() => (window as unknown as { __talk: (ms: number) => void }).__talk(1500))

  // The words reach the conversation as an ordinary turn.
  await expect.poll(() => sends.length, { timeout: 15_000 }).toBe(1)
  expect(sends[0].body.content).toBe(spoken)

  // Read aloud: sentences, without the table, the code or the citation number.
  await expect.poll(() => speaks.filter((s) => !s.warm).length, { timeout: 15_000 }).toBeGreaterThan(0)
  await expect(panel).toHaveAttribute('data-phase', 'listening', { timeout: 15_000 })
  const read = speaks.filter((s) => !s.warm)
  const text = read.map((s) => s.text).join(' ')
  expect(text).toContain('정리해 드릴게요')
  expect(text).toContain('이상입니다')
  expect(text).not.toMatch(/\||\[1\]|print|2억/)
  expect(read.every((s) => s.voice === 'sohee')).toBe(true)
  // The model was warmed once on entry, with a clip nobody hears.
  expect(speaks.filter((s) => s.warm)).toHaveLength(1)

  // The next question is sent to Whisper like the first, without the earlier answer as a hint.
  await page.evaluate(() => (window as unknown as { __talk: (ms: number) => void }).__talk(1500))
  await expect.poll(() => sends.length, { timeout: 15_000 }).toBe(2)
  await expect(panel).toHaveAttribute('data-phase', 'listening', { timeout: 15_000 })
  expect(transcriptions).toHaveLength(2)
  expect(prompted).toEqual([false, false])

  await page.getByTestId('voice-end').click()
  await expect(panel).toHaveCount(0)
  expect(unexpected).toEqual([])
})

test('말은 들렸는데 글이 비면 오류가 아니라 안내를 보이고, 연달아 비면 마이크를 가까이 하라고 한다', async ({ page }, testInfo) => {
  await fakeMicrophone(page)
  const { transcriptions } = await stub(page, { withVoice: true, silent: true })
  const { sends, unexpected } = await composerFixture(page, testInfo)
  await page.goto('/new/chat')
  await page.getByTestId('voice-chat-button').click()
  const panel = page.getByTestId('voice-panel')
  const heard = page.getByTestId('voice-heard')
  await expect(page.getByTestId('voice-status')).toHaveText('듣고 있습니다. 말씀하세요')
  await expect(heard).toHaveText('말씀하시면 여기에 받아쓴 글이 보입니다.')

  const talk = () => page.evaluate(() => (window as unknown as { __talk: (ms: number) => void }).__talk(1500))
  await talk()
  await expect.poll(() => transcriptions.length, { timeout: 15_000 }).toBe(1)
  await expect(heard).toHaveText('말씀은 들렸지만 글로 옮기지 못했습니다. 다시 말씀해 주세요.')
  await expect(panel).toHaveAttribute('data-phase', 'listening')

  await expect(panel).toHaveAttribute('data-phase', 'listening')
  await talk()
  await expect.poll(() => transcriptions.length, { timeout: 15_000 }).toBe(2)
  await expect(panel).toHaveAttribute('data-phase', 'listening')
  await talk()
  await expect.poll(() => transcriptions.length, { timeout: 15_000 }).toBe(3)
  await expect(heard).toHaveText('잘 들리지 않습니다. 마이크 가까이에서 말씀하세요.')
  // Nothing was sent as a question.
  expect(sends).toHaveLength(0)
  expect(unexpected.filter((u) => !/transcriptions/.test(String(u)))).toEqual([])
})

test('목소리 모델이 없으면 음성 대화 버튼이 보이지 않는다', async ({ page }, testInfo) => {
  await fakeMicrophone(page)
  await stub(page, { withVoice: false })
  await composerFixture(page, testInfo)
  await page.goto('/new/chat')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await expect(page.getByTestId('voice-chat-button')).toHaveCount(0)
})
