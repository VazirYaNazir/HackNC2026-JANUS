import { describe, expect, it } from 'vitest';
import { withIpv4First } from '../lib/common.mjs';
import { normalizeLauncherArgs } from '../start.mjs';

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

describe('shared-launcher iMessage requests', () => {
  it('preserves an explicit sidecar request for Python startup and monitoring', () => {
    expect(normalizeLauncherArgs(['--imessage'], {})).toEqual(['--imessage']);
  });

  it('recovers the request when npm swallowed its flag', () => {
    expect(normalizeLauncherArgs([], { npm_config_imessage: 'true' })).toEqual(['--imessage']);
  });
});
