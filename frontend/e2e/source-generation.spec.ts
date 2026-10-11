import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const BACKEND = 'http://127.0.0.1:18980'
const headers = { Authorization: 'Bearer control-2-' + 'b'.repeat(32) }

test('Studio generates, stores and downloads source offline and without an adapter, without changing bindings or dispatching jobs', async ({ page, request }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  expect((await request.post(`${BACKEND}/test/source-agent/disconnect`)).ok()).toBe(true)
  const metricsBefore = await (await request.get(`${BACKEND}/test/metrics`)).json()
  const jobsBefore = await (await request.get(`${BACKEND}/api/v1/agent-jobs`, { headers })).json()
  await page.goto('http://127.0.0.1:18981')
  await page.getByRole('button', { name: 'Connect backend' }).click()
  await page.getByLabel('Backend address').fill(BACKEND)
  await page.getByLabel('Laptop controller credential').fill('control-2-' + 'b'.repeat(32))
  await page.getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Laptop offline · connected' })).toBeVisible()
  await page.getByRole('button', { name: 'Project', exact: true }).click()
  await page.getByRole('button', { name: 'Laptop controls starter' }).click()
  await page.getByRole('button', { name: 'Save & Push' }).click()
  await expect(page.getByText('Panel saved and pushed to paired phones.')).toBeVisible()
  const savedLayout = await (await request.get(`${BACKEND}/api/layout`, { headers })).json()
  await expect(page.getByLabel('AI assistant prompt')).toBeEnabled()
  await page.locator('.assistant-options > summary').click()
  await expect(page.getByLabel('Platform')).toHaveValue('Darwin')
  await page.locator('.assistant-options > summary').click()
  // No connected agent is required to submit with an explicit default target.
  const outgoing = page.waitForRequest(value => value.url().endsWith('/api/v1/programs/generate'))
  const generated = page.waitForResponse(value => value.url().endsWith('/api/v1/programs/generate'))
  await page.getByLabel('AI assistant prompt').fill('fixture')
  await page.getByRole('button', { name: 'Generate', exact: true }).click()
  const generationRequest = (await outgoing).postDataJSON()
  const response = await generated
  expect(response.status()).toBe(201)
  const draft = await response.json()
  const review = page.getByRole('dialog', { name: 'Generation complete' })
  await expect(review.getByLabel('C source')).toHaveText(draft.program.source)
  await expect(review.getByText('This source has not been validated or executed.')).toBeVisible()
  await expect(review.getByText(/Connect a laptop with a build and execution isolation adapter/)).toBeVisible()
  const downloading = page.waitForEvent('download')
  await review.getByRole('button', { name: 'Download C source' }).click()
  const download = await downloading
  expect(download.suggestedFilename()).toBe('janus-generated.c')
  const filename = await download.path()
  expect(filename).not.toBeNull()
  expect(await readFile(filename!, 'utf8')).toBe(draft.program.source)
  const similar = await request.post(`${BACKEND}/api/v1/programs/similar`, { headers, data: generationRequest })
  expect(similar.ok()).toBe(true)
  expect((await similar.json()).matches).toContainEqual(expect.objectContaining({ id: draft.program_id, match: 'exact' }))
  expect(await (await request.get(`${BACKEND}/api/layout`, { headers })).json()).toEqual(savedLayout)
  expect(await (await request.get(`${BACKEND}/api/v1/agent-jobs`, { headers })).json()).toEqual(jobsBefore)
  await review.getByRole('button', { name: 'Close', exact: true }).click()

  try {
    expect((await request.post(`${BACKEND}/test/source-agent/connect`)).ok()).toBe(true)
    await expect(page.getByRole('button', { name: 'Laptop online · connected' })).toBeVisible()
    await expect(page.getByRole('status').filter({ hasText: 'running generated programs requires an isolation adapter' })).toBeVisible()
    // Selecting a native control must still produce a source draft rather than
    // replacing its working native binding when either boundary is unavailable.
    await page.getByRole('button', { name: 'Left click', exact: true }).click()
    const responseAgain = page.waitForResponse(value => value.url().endsWith('/api/v1/programs/generate'))
    await page.getByLabel('AI assistant prompt').fill('fixture without adapter')
    await page.getByRole('button', { name: 'Generate', exact: true }).click()
    expect((await responseAgain).status()).toBe(201)
    await expect(page.getByRole('dialog', { name: 'Generation complete' }).getByText(/Execution is unavailable on this laptop/)).toBeVisible()
    expect(await (await request.get(`${BACKEND}/api/layout`, { headers })).json()).toEqual(savedLayout)
    expect(await (await request.get(`${BACKEND}/api/v1/agent-jobs`, { headers })).json()).toEqual(jobsBefore)
    const after = await (await request.get(`${BACKEND}/test/metrics`)).json()
    expect(after.generation_calls - metricsBefore.generation_calls).toBe(2)
    expect(after.source_agent_jobs).toBe(0)
    expect(errors).toEqual([])
  } finally {
    await request.post(`${BACKEND}/test/source-agent/disconnect`)
  }
})
