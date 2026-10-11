import { chromium, expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BACKEND = 'http://127.0.0.1:18980'
const PHONE = 'http://127.0.0.1:18982'
/** The pairing link a user pastes into the PWA as an alternative to address/code entry. */
const linkFor = (code: string) => `${BACKEND}?pairing_code=${code}`
const headers = (device = 1) => ({ Authorization: `Bearer control-${device}-` + 'b'.repeat(32) })

async function createCode(request: APIRequestContext, device = 1) {
  const response = await request.post(`${BACKEND}/api/v1/studio/pairing`, { headers: headers(device) })
  expect(response.ok()).toBe(true)
  const result = await response.json() as { code: string; host: string }
  expect(result.code).toMatch(/^[0-9A-F]{12}$/)
  return result
}

async function studio(page: Page) {
  await page.goto('http://127.0.0.1:18981')
  await page.getByRole('button', { name: 'Connect backend' }).click()
  await page.getByLabel('Backend address').fill(BACKEND)
  await page.getByLabel('Laptop controller credential').fill('control-1-' + 'b'.repeat(32))
  await page.getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(page.getByRole('button', { name: /Laptop online · connected/ })).toBeVisible()
  const response = page.waitForResponse((value) => value.url().endsWith('/api/v1/studio/pairing'))
  await page.getByRole('button', { name: 'Pair Device' }).click()
  const result = await (await response).json() as { code: string; host: string }
  await expect(page.getByRole('img', { name: 'JANUS pairing QR code' })).toBeVisible()
  return result
}

async function manual(page: Page, code: string) {
  await page.goto(PHONE)
  await page.getByRole('button', { name: 'Enter code instead', exact: true }).click()
  await page.getByLabel('Pairing link', { exact: true }).fill(linkFor(code))
}

async function save(page: Page, name: string) {
  await page.getByLabel('Panel name').fill(name)
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await page.getByText(name, { exact: true }).click()
  await expect(page.getByText('Laptop online · connected', { exact: true })).toBeVisible()
}

test('manual code entry pairs without camera access and receives published layout updates', async ({ page, context, request }) => {
  const { code, host } = await studio(page)
  await expect(page.getByLabel('Phone backend address')).toHaveValue(host)
  const phone = await context.newPage()
  await phone.setViewportSize({ width: 390, height: 844 })
  await manual(phone, code.toLowerCase())
  await phone.getByRole('button', { name: 'Backend and code', exact: true }).click()
  await phone.getByLabel('PC address', { exact: true }).fill(BACKEND)
  await phone.getByLabel('Pairing code', { exact: true }).fill(code.toLowerCase())
  await phone.getByRole('button', { name: 'Pair', exact: true }).click()
  await save(phone, 'Manual desk')
  const current = await (await request.get(`${BACKEND}/api/layout`, { headers: headers() })).json()
  const response = await request.put(`${BACKEND}/api/v1/studio/layout`, { headers: headers(), data: {
    revision: current.revision,
    layout: { schemaVersion: 2, coordinateSpace: 'logical', canvas: { width: 360, height: 780 }, name: 'Phone link test',
      widgets: [{ id: 'phone-link', type: 'button', action: 'compute', label: 'Published on phone', x: 0, y: 0, w: 168, h: 62 }] },
  } })
  expect(response.ok()).toBe(true)
  await expect(phone.getByRole('button', { name: 'Published on phone', exact: true })).toBeVisible()
  await phone.reload()
  await phone.getByText('Manual desk', { exact: true }).click()
  await expect(phone.getByRole('button', { name: 'Published on phone', exact: true })).toBeVisible()
})

test('pasting the exact pairing link displayed by Studio links the phone', async ({ page, context }) => {
  await studio(page)
  const link = await page.getByLabel('Pairing link', { exact: true }).inputValue()
  const phone = await context.newPage()
  await phone.goto(PHONE)
  await phone.getByRole('button', { name: 'Enter code instead', exact: true }).click()
  await phone.getByLabel('Pairing link', { exact: true }).fill(link)
  await phone.getByRole('button', { name: 'Pair', exact: true }).click()
  await save(phone, 'Pasted desk')
})

test('a link opening the PWA fills the form, waits for approval, and retains pairing before naming', async ({ page, request }) => {
  const { code } = await createCode(request)
  const link = new URL(PHONE)
  link.searchParams.set('backend', BACKEND)
  link.searchParams.set('pairing_code', code)
  let submissions = 0
  page.on('request', (value) => { if (value.url() === `${BACKEND}/api/pair`) submissions++ })
  await page.goto(link.toString())
  await expect(page.getByLabel('Pairing link', { exact: true })).toHaveValue(`${BACKEND}/?pairing_code=${code}`)
  expect(submissions).toBe(0)
  await expect(page).toHaveURL(PHONE + '/')
  await page.getByRole('button', { name: 'Pair', exact: true }).click()
  await expect(page.getByLabel('Panel name')).toBeVisible()
  const saved = await page.evaluate(() => (JSON.parse(localStorage.getItem('janus.panels') ?? '[]') as { name: string; agentDeviceId?: string }[]).filter((panel) => panel.agentDeviceId === 'device-1'))
  expect(saved).toHaveLength(1)
  // Reload before Save: the one-time code has been spent, but its credential is already retained.
  await page.reload()
  await page.locator('.card__main').filter({ hasText: saved[0].name }).click()
  await expect(page.getByText('Laptop online · connected', { exact: true })).toBeVisible()
  expect(submissions).toBe(1)
})

test('an expired code stays on the manual form and a new code allows retry', async ({ page, request }) => {
  const { code } = await createCode(request)
  expect((await request.post(`${BACKEND}/test/pairing/${code}/expire`)).ok()).toBe(true)
  await manual(page, code)
  await page.getByRole('button', { name: 'Pair', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Create a new code in Studio')
  await expect(page.getByLabel('Pairing link', { exact: true })).toHaveValue(linkFor(code))
  const fresh = await createCode(request)
  await page.getByLabel('Pairing link', { exact: true }).fill(linkFor(fresh.code))
  await page.getByRole('button', { name: 'Pair', exact: true }).click()
  await save(page, 'Retried desk')
})

test('a code used by one phone cannot be replayed by a second phone', async ({ page, request }) => {
  const { code } = await createCode(request)
  expect((await request.post(`${BACKEND}/api/pair`, { data: { code, name: 'First phone' } })).ok()).toBe(true)
  await manual(page, code)
  await page.getByRole('button', { name: 'Pair', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('already been used')
  await expect(page.getByLabel('Pairing link', { exact: true })).toHaveValue(linkFor(code))
  expect(await page.evaluate(() => (JSON.parse(localStorage.getItem('janus.panels') ?? '[]') as { agentDeviceId?: string }[]).filter((panel) => panel.agentDeviceId))).toHaveLength(0)
})

test('network failure preserves entered details and retries the same unconsumed code', async ({ page, request }) => {
  const { code } = await createCode(request)
  await manual(page, code)
  await page.route(`${BACKEND}/api/pair`, (route) => route.abort('connectionrefused'))
  await page.getByRole('button', { name: 'Pair', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Could not reach the backend')
  await expect(page.getByLabel('Pairing link', { exact: true })).toHaveValue(linkFor(code))
  await page.unroute(`${BACKEND}/api/pair`)
  await page.getByRole('button', { name: 'Pair', exact: true }).click()
  await save(page, 'Network retry')
})

test('camera unavailability leaves manual pairing available', async ({ page, request }) => {
  const { code } = await createCode(request)
  await page.goto(PHONE)
  await page.getByRole('button', { name: 'Scan to add panel' }).click()
  await expect(page.getByRole('alert')).toContainText('Camera unavailable')
  await page.getByRole('button', { name: 'Enter code instead', exact: true }).click()
  await page.getByLabel('Pairing link', { exact: true }).fill(linkFor(code))
  await page.getByRole('button', { name: 'Pair', exact: true }).click()
  await save(page, 'No camera desk')
})

test('Studio removes expired QR codes and generates a fresh code on demand', async ({ page }) => {
  await page.clock.install()
  const { code } = await studio(page)
  await page.clock.fastForward(61000)
  await expect(page.getByRole('dialog', { name: 'Pair your phone' }).getByRole('status')).toContainText('Code expired')
  await expect(page.getByRole('img', { name: 'JANUS pairing QR code' })).toHaveCount(0)
  const response = page.waitForResponse((value) => value.url().endsWith('/api/v1/studio/pairing'))
  await page.getByRole('button', { name: 'New code' }).click()
  expect((await (await response).json()).code).not.toBe(code)
  await expect(page.getByRole('img', { name: 'JANUS pairing QR code' })).toBeVisible()
})

test('re-pairing preserves panel identity and rejects a code belonging to another laptop', async ({ page, request }) => {
  const first = await createCode(request)
  await manual(page, first.code)
  const exchange = page.waitForResponse((value) => value.url() === `${BACKEND}/api/pair`)
  await page.getByRole('button', { name: 'Pair', exact: true }).click()
  const phoneId = (await (await exchange).json()).deviceId
  await save(page, 'Keep this name')
  const original = await page.evaluate(() => (JSON.parse(localStorage.getItem('janus.panels') ?? '[]') as { id: string; name: string; token: string }[]).find((panel) => panel.name === 'Keep this name'))
  expect((await request.post(`${BACKEND}/test/phones/${phoneId}/revoke`)).ok()).toBe(true)
  await page.reload()
  await page.getByText('Keep this name', { exact: true }).click()
  await expect(page.getByRole('button', { name: 'Pair again' })).toBeVisible()
  await page.getByRole('button', { name: 'Pair again' }).click()
  await page.getByRole('button', { name: 'Enter code instead', exact: true }).click()
  const wrongLaptop = await createCode(request, 2)
  await page.getByLabel('Pairing link', { exact: true }).fill(linkFor(wrongLaptop.code))
  await page.getByRole('button', { name: 'Pair', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('different PC')
  const unchanged = await page.evaluate(() => (JSON.parse(localStorage.getItem('janus.panels') ?? '[]') as { id: string; name: string; token: string }[]).find((panel) => panel.name === 'Keep this name'))
  expect(unchanged).toMatchObject({ id: original?.id, name: original?.name, token: original?.token })
  const correctLaptop = await createCode(request)
  await page.getByLabel('Pairing link', { exact: true }).fill(linkFor(correctLaptop.code))
  await page.getByRole('button', { name: 'Pair', exact: true }).click()
  await expect(page.getByText('Laptop online · connected', { exact: true })).toBeVisible()
  const repaired = await page.evaluate(() => (JSON.parse(localStorage.getItem('janus.panels') ?? '[]') as { id: string; name: string; token: string }[]).find((panel) => panel.name === 'Keep this name'))
  expect(repaired?.id).toBe(original?.id)
  expect(repaired?.token).not.toBe(original?.token)
  // Re-pairing is also reachable from the home menu if a tunnel's old address
  // no longer responds and cannot return an explicit authorization error.
  await page.getByRole('button', { name: 'Back to panels' }).click()
  await page.getByRole('button', { name: 'Options for Keep this name' }).click()
  await page.getByRole('button', { name: 'Pair again', exact: true }).click()
  await expect(page.getByLabel('Pairing link', { exact: true })).toHaveValue('')
})

test('the real phone camera scanner decodes Studio’s rendered QR and pairs exactly once', async ({ page }) => {
  await studio(page)
  // Feed the actual displayed QR to Chromium's fake video device. The app still
  // runs its real getUserMedia + html5-qrcode decoder and its real HTTP exchange.
  const pixels = await page.getByRole('img', { name: 'JANUS pairing QR code' }).evaluate(async (element: HTMLImageElement) => {
    await element.decode()
    const canvas = document.createElement('canvas')
    canvas.width = 640; canvas.height = 480
    const context = canvas.getContext('2d')!
    context.fillStyle = '#ffffff'; context.fillRect(0, 0, 640, 480)
    context.drawImage(element, 192, 112, 256, 256)
    const rgba = context.getImageData(0, 0, 640, 480).data
    return Array.from({ length: 640 * 480 }, (_, index) => rgba[index * 4])
  })
  expect(pixels.filter((value) => value < 128).length).toBeGreaterThan(10000)
  const folder = await mkdtemp(join(tmpdir(), 'janus-qr-camera-'))
  const capture = join(folder, 'qr.y4m')
  const y = Buffer.from(pixels.map((value) => 16 + Math.round(219 * value / 255)))
  const frame = Buffer.concat([Buffer.from('FRAME\n'), y, Buffer.alloc(640 * 480 / 2, 128)])
  await writeFile(capture, Buffer.concat([Buffer.from('YUV4MPEG2 W640 H480 F10:1 Ip A1:1 C420jpeg\n'), ...Array.from({ length: 30 }, () => frame)]))
  const camera = await chromium.launch({ args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', `--use-file-for-fake-video-capture=${capture}`] })
  try {
    const phone = await camera.newPage({ viewport: { width: 390, height: 844 }, permissions: ['camera'] })
    let exchanges = 0
    phone.on('request', (value) => { if (value.url() === `${BACKEND}/api/pair`) exchanges++ })
    await phone.goto(PHONE)
    await phone.getByRole('button', { name: 'Scan to add panel' }).click()
    await expect(phone.getByLabel('Panel name')).toBeVisible({ timeout: 20000 })
    await expect(phone.locator('video')).toHaveCount(0)
    await save(phone, 'QR desk')
    expect(exchanges).toBe(1)
    await phone.reload()
    await phone.getByText('QR desk', { exact: true }).click()
    await expect(phone.getByText('Laptop online · connected', { exact: true })).toBeVisible()
  } finally {
    await camera.close()
    await rm(folder, { recursive: true, force: true })
  }
})
