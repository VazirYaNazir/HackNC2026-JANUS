import { expect, test } from '@playwright/test';

test('Studio starter, paired phone, native controls and all laptop streams work through the real backend', async ({ page, context, request }) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('http://127.0.0.1:18981');
  await page.getByRole('button', { name: 'Connect backend' }).click();
  await page.getByLabel('Backend address').fill('http://127.0.0.1:18980');
  await page.getByLabel('Laptop controller credential').fill('control-1-' + 'b'.repeat(32));
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByRole('button', { name: /Laptop online · connected/ })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Janus', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Project', exact: true }).click();
  await page.getByRole('button', { name: 'Laptop controls starter' }).click();
  await page.getByLabel('Project name').fill('My laptop'); await page.getByLabel('Project name').press('Enter');
  await page.getByRole('button', { name: 'Save & Push' }).click();
  await expect(page.getByText('Panel saved and pushed to paired phones.')).toBeVisible();
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  await expect(page.getByRole('slider', { name: 'Volume' })).toHaveValue('25');
  await expect(page.getByRole('table', { name: 'Relevant processes' })).toContainText('Busy app');

  const pairingResponse = page.waitForResponse(r => r.url().endsWith('/api/v1/studio/pairing'));
  await page.getByRole('button', { name: 'Pair Device' }).click();
  const pairing = await (await pairingResponse).json(); await page.getByRole('button', { name: 'Close', exact: true }).click();
  const phone = await context.newPage(); phone.on('pageerror', e => errors.push(e.message));
  await phone.setViewportSize({ width: 390, height: 844 }); await phone.goto('http://127.0.0.1:18982');
  await phone.getByRole('button', { name: 'Enter code instead' }).click();
  await phone.getByLabel('PC address').fill('http://127.0.0.1:18980'); await phone.getByLabel('Pairing code').fill(pairing.code);
  await phone.getByRole('button', { name: 'Pair', exact: true }).click(); await phone.getByLabel('Panel name').fill('Native laptop');
  await phone.getByRole('button', { name: 'Save', exact: true }).click(); await phone.getByText('Native laptop', { exact: true }).click();
  await expect(phone.getByRole('table', { name: 'Relevant processes' })).toContainText('Busy app');
  await expect(phone.locator('.jw--temperature')).toContainText('55'); await expect(phone.locator('.jw--bar')).toContainText('42');
  const before = await (await request.get('http://127.0.0.1:18980/test/metrics')).json();
  await phone.getByRole('slider', { name: 'Volume' }).press('End');
  await expect(phone.getByRole('slider', { name: 'Volume' })).toHaveValue('100');
  await expect(page.getByRole('slider', { name: 'Volume' })).toHaveValue('100');
  const stick = phone.getByRole('group', { name: /Mouse joystick/ }); await stick.focus(); await phone.keyboard.down('ArrowRight');
  await expect.poll(async () => (await (await request.get('http://127.0.0.1:18980/test/metrics')).json()).native_commands.some((c: { action: string; args: { x?: number } }) => c.action === 'move' && c.args.x === 1)).toBe(true);
  await phone.keyboard.up('ArrowRight');
  await expect.poll(async () => {
    const metrics = await (await request.get('http://127.0.0.1:18980/test/metrics')).json();
    const last = metrics.native_commands.filter((c: { action: string }) => c.action === 'move').at(-1);
    return last?.args;
  }).toEqual({ x: 0, y: 0 });
  await phone.getByRole('button', { name: 'Left click', exact: true }).click();
  await expect.poll(async () => {
    const metrics = await (await request.get('http://127.0.0.1:18980/test/metrics')).json();
    return metrics.native_commands.filter((c: { action: string }) => c.action === 'button').slice(-2).map((c: { args: { state: string } }) => c.args.state);
  }).toEqual(['down', 'up']);
  const jpeg = await phone.evaluate(() => { const canvas = document.createElement('canvas'); canvas.width = 80; canvas.height = 50; const draw = canvas.getContext('2d')!; draw.fillStyle = '#F5A524'; draw.fillRect(0, 0, 80, 50); return canvas.toDataURL('image/jpeg').split(',')[1]; });
  await request.post('http://127.0.0.1:18980/test/native/screen', { data: { base64: jpeg } });
  await expect(phone.locator('.jw--stream img')).toBeVisible(); await expect(page.locator('.jw--stream img')).toBeVisible();
  await expect.poll(() => phone.locator('.jw--stream img').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(80);
  const after = await (await request.get('http://127.0.0.1:18980/test/metrics')).json(); expect(after.generation_calls).toBe(before.generation_calls);
  expect(errors).toEqual([]);
});
