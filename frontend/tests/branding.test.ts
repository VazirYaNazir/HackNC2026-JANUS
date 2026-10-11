import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const read = (path: string) => readFileSync(new URL(`../../${path}`, import.meta.url));
describe('supplied Janus branding assets', () => {
  it.each([
    ['frontend/public/janus-logo.svg', 'janus-logo-kit/janus-logo-transparent.svg'],
    ['PWA/public/janus-logo.svg', 'janus-logo-kit/janus-logo-transparent.svg'],
    ['frontend/public/favicon.svg', 'janus-logo-kit/janus-logo.svg'],
    ['PWA/public/icon.svg', 'janus-logo-kit/janus-logo.svg'],
    ['frontend/public/favicon.ico', 'janus-logo-kit/favicon.ico'],
    ['frontend/public/icon-512.png', 'janus-logo-kit/png/janus-512.png'],
    ['PWA/public/icon-192.png', 'janus-logo-kit/png/janus-192.png'],
    ['PWA/public/icon-512.png', 'janus-logo-kit/png/janus-512.png'],
    ['PWA/public/icon-maskable-512.png', 'janus-logo-kit/png/janus-maskable-512.png'],
    ['PWA/public/apple-touch-icon.png', 'janus-logo-kit/png/apple-touch-icon.png'],
  ])('%s uses the supplied logo without asset drift', (actual, expected) => { expect(read(actual).equals(read(expected))).toBe(true); });
  it.each([192, 512])('ships a valid %s-pixel install icon', pixels => {
    const file = read(`PWA/public/icon-${pixels}.png`); expect(file.subarray(1, 4).toString()).toBe('PNG');
    expect(file.readUInt32BE(16)).toBe(pixels); expect(file.readUInt32BE(20)).toBe(pixels);
  });
});
