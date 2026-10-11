// Pinned cloudflared download. The SHA-256 values live in this repo, so a tampered download (or release page)
// is rejected rather than trusted because it matches a checksum fetched from the same place.
import { createHash } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { JANUS_DIR, capture, isWindows, which } from './common.mjs';

export const CLOUDFLARED_VERSION = '2026.10.0';

// Hashes of the official GitHub release assets for CLOUDFLARED_VERSION. To bump: download the new assets,
// verify them against Cloudflare's published release, and update both the version and these values.
const ASSETS = {
  'win32-x64': { file: 'cloudflared-windows-amd64.exe', sha256: '86aee4017b26625cee8484c113558f48effa4cd47f7aa05fcf425604e5d2b23c' },
  'linux-x64': { file: 'cloudflared-linux-amd64', sha256: 'd33ff2d14475178d2012c2c56beba87389ac5ded27649519f198a7d3134a99db' },
  'linux-arm64': { file: 'cloudflared-linux-arm64', sha256: 'e6422b9d4f72d3194bc5a38676f13667c06666523217b842a877d72a80b5ac08' },
  'darwin-x64': { file: 'cloudflared-darwin-amd64.tgz', sha256: '903845b81828c8cb3c5d13d816a2de71c06a3da5785469df8eb0e1b736d92f9f', tgz: true },
  'darwin-arm64': { file: 'cloudflared-darwin-arm64.tgz', sha256: 'a2f79ff7b9420aa537d74af239f376da170bbabeb529aec416002adac6a72e70', tgz: true },
};

export const BIN_DIR = path.join(JANUS_DIR, 'bin');
const LOCAL_BINARY = path.join(BIN_DIR, isWindows ? 'cloudflared.exe' : 'cloudflared');

/** Path to a usable cloudflared (repo-local pinned copy first, then PATH), or null. */
export function findCloudflared() {
  return existsSync(LOCAL_BINARY) ? LOCAL_BINARY : which('cloudflared');
}

export async function ensureCloudflared() {
  const existing = findCloudflared();
  if (existing) return existing;
  const asset = ASSETS[`${process.platform}-${process.arch}`];
  if (!asset) throw new Error(`No pinned cloudflared build for ${process.platform}-${process.arch}; install it manually.`);

  console.log(`Downloading cloudflared ${CLOUDFLARED_VERSION}…`);
  const url = `https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/${asset.file}`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`cloudflared download failed (${response.status})`);
  const bytes = Buffer.from(await response.arrayBuffer());
  const actual = createHash('sha256').update(bytes).digest('hex');
  if (actual !== asset.sha256) throw new Error(`cloudflared checksum mismatch (expected ${asset.sha256}, got ${actual}); refusing to install.`);

  mkdirSync(BIN_DIR, { recursive: true });
  const staged = path.join(BIN_DIR, `${asset.file}.download`);
  writeFileSync(staged, bytes);
  if (asset.tgz) {
    const result = capture('tar', ['-xzf', staged, '-C', BIN_DIR, 'cloudflared']);
    rmSync(staged, { force: true });
    if (result.code !== 0) throw new Error(`Could not extract cloudflared: ${result.stderr}`);
  } else {
    renameSync(staged, LOCAL_BINARY);
  }
  if (!isWindows) chmodSync(LOCAL_BINARY, 0o755);
  const check = capture(LOCAL_BINARY, ['--version']);
  if (check.code !== 0) throw new Error('Downloaded cloudflared did not run.');
  console.log(`Installed ${check.stdout.trim()}`);
  return LOCAL_BINARY;
}
