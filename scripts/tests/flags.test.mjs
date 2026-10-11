import { describe, expect, it } from 'vitest';
import { makeFlags } from '../lib/common.mjs';

describe('makeFlags', () => {
  it('reads ordinary flags from argv', () => {
    const flags = makeFlags(['--imessage', '--studio=browser', '--pwa-url=https://a.b/c=d'], {});
    expect(flags.has('imessage')).toBe(true);
    expect(flags.has('tunnel')).toBe(false);
    expect(flags.value('studio')).toBe('browser');
    expect(flags.value('pwa-url')).toBe('https://a.b/c=d');
    expect(flags.value('only')).toBeUndefined();
  });

  it('reads flags npm swallowed (PowerShell drops the -- separator)', () => {
    const flags = makeFlags([], { npm_config_imessage: 'true', npm_config_screen: 'true', npm_config_studio: 'none', npm_config_skip_native: 'true' });
    expect(flags.has('imessage')).toBe(true);
    expect(flags.has('screen')).toBe(true);
    expect(flags.has('skip-native')).toBe(true);
    expect(flags.has('tunnel')).toBe(false);
    expect(flags.value('studio')).toBe('none');
  });

  it('understands --no-agent arriving as npm_config_agent=false (or empty)', () => {
    expect(makeFlags([], { npm_config_agent: 'false' }).has('no-agent')).toBe(true);
    expect(makeFlags([], { npm_config_agent: '' }).has('no-agent')).toBe(true);
    expect(makeFlags([], {}).has('no-agent')).toBe(false);
    expect(makeFlags(['--no-agent'], {}).has('no-agent')).toBe(true);
    expect(makeFlags([], { npm_config_agent: 'true' }).has('no-agent')).toBe(false);
  });

  it('does not mistake unrelated or boolean-looking config for values', () => {
    const flags = makeFlags([], { npm_config_studio: 'true', npm_config_only: 'false', npm_config_pwa: 'yes' });
    expect(flags.value('studio')).toBeUndefined();
    expect(flags.value('only')).toBeUndefined();
    expect(flags.has('pwa')).toBe(false);
  });

  it('prefers an explicit argv value over npm config', () => {
    expect(makeFlags(['--studio=browser'], { npm_config_studio: 'none' }).value('studio')).toBe('browser');
  });
});
