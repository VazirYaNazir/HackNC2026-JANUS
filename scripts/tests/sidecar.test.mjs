import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { withIpv4First } from '../lib/common.mjs';

describe('withIpv4First', () => {
  it('adds IPv4-first DNS when NODE_OPTIONS is empty or unset', () => {
    expect(withIpv4First(undefined)).toBe('--dns-result-order=ipv4first');
    expect(withIpv4First('')).toBe('--dns-result-order=ipv4first');
  });

  it('preserves whatever the user already has', () => {
    expect(withIpv4First('--max-old-space-size=4096')).toBe('--max-old-space-size=4096 --dns-result-order=ipv4first');
    expect(withIpv4First('--max-old-space-size=4096 --trace-warnings')).toBe('--max-old-space-size=4096 --trace-warnings --dns-result-order=ipv4first');
  });

  it('puts ours last so it wins over an earlier dns-result-order', () => {
    expect(withIpv4First('--dns-result-order=verbatim')).toBe('--dns-result-order=verbatim --dns-result-order=ipv4first');
  });

  it('reads the current environment by default', () => {
    const saved = process.env.NODE_OPTIONS;
    try {
      process.env.NODE_OPTIONS = '--trace-warnings';
      expect(withIpv4First()).toBe('--trace-warnings --dns-result-order=ipv4first');
      delete process.env.NODE_OPTIONS;
      expect(withIpv4First()).toBe('--dns-result-order=ipv4first');
    } finally {
      if (saved === undefined) delete process.env.NODE_OPTIONS; else process.env.NODE_OPTIONS = saved;
    }
  });
});

describe('start.mjs wiring for the sidecar', () => {
  const source = readFileSync(new URL('../start.mjs', import.meta.url), 'utf8');

  it('always launches the sidecar with merged IPv4-first NODE_OPTIONS', () => {
    expect(source).toContain('NODE_OPTIONS: withIpv4First()');
  });

  it('prints a loud red line when the sidecar stops and tells the backend straight away', () => {
    expect(source).toContain("RED = '\\x1b[1;31m'");
    expect(source).toContain('iMessage sidecar stopped — texts will not be received');
    expect(source).toContain('markSidecarDown(');
    expect(source).toContain('JSON.stringify({ connected: false })');
  });
});
