import { describe, expect, it } from 'vitest';
import { executionUnavailable, generationTarget, type Session } from '@janus/client';
import { parseServerMessage, type GenerationRequest } from '@janus/schema';

const session: Session = { device_id: 'laptop', online: true, target: { platform: 'Windows', architecture: 'x86_64', build_boundary_available: true, execution_boundary_available: true, execution_target: { platform: 'Linux', architecture: 'x86_64', profile: 'portable-c', capabilities: ['files.read', 'audio.volume'], dependencies: [] } } };
const request: GenerationRequest = { description: 'Set volume', operation: 'volume', platform: 'Linux', architecture: 'x86_64', capabilities: ['audio.volume'], dependencies: [], output_protocol: 'json-v1' };

describe('portable execution readiness', () => {
  it('uses the isolated execution target and preserves legacy physical targets', () => {
    expect(generationTarget(session)).toEqual(session.target!.execution_target);
    const legacy = { ...session, target: { ...session.target!, execution_target: undefined } };
    expect(generationTarget(legacy)).toEqual(legacy.target);
    expect(generationTarget(null)).toBeNull();
  });
  it('allows exact supported scope and explains offline, target, capability and library failures', () => {
    expect(executionUnavailable(session, request)).toBeNull();
    expect(executionUnavailable(null, request)).toContain('Connect your laptop agent');
    expect(executionUnavailable({ ...session, online: false }, request)).toContain('Connect your laptop agent');
    expect(executionUnavailable(session, { ...request, platform: 'Windows' })).toContain('different system');
    expect(executionUnavailable(session, { ...request, architecture: 'arm64' })).toContain('different system');
    expect(executionUnavailable(session, { ...request, capabilities: ['network', 'admin'] })).toContain('network, admin');
    expect(executionUnavailable(session, { ...request, dependencies: ['win32'] })).toContain('win32');
  });
  it('explains Docker startup failures before execution can be requested', () => {
    expect(executionUnavailable({ ...session, target: { ...session.target!, execution_boundary_available: false, boundary_reason: 'DOCKER_UNAVAILABLE' } })).toContain('Start Docker Desktop');
    expect(executionUnavailable({ ...session, target: { ...session.target!, build_boundary_available: false, boundary_reason: 'WORKER_IMAGE_UNAVAILABLE' } })).toContain('WORKER_IMAGE_UNAVAILABLE');
    for (const [reason, instruction] of [['DOCKER_NOT_INSTALLED', 'Install and start Docker Desktop'], ['DOCKER_NOT_RUNNING', 'Start Docker Desktop'], ['DOCKER_LINUX_ENGINE_REQUIRED', 'Linux containers'], ['DOCKER_LOCAL_ENGINE_REQUIRED', 'local Docker Desktop engine'], ['WORKER_ARCHITECTURE_UNSUPPORTED', 'arm64 or x86_64']]) expect(executionUnavailable({ ...session, target: { ...session.target!, execution_boundary_available: false, boundary_reason: reason } })).toContain(instruction);
  });
  it('accepts valid portable presence and rejects malformed execution target fields', () => {
    expect(parseServerMessage(JSON.stringify({ type: 'agent_presence', session }))).toEqual({ type: 'agent_presence', session });
    const headless = { ...session, target: { ...session.target!, platform: 'Linux' } };
    expect(parseServerMessage(JSON.stringify({ type: 'agent_presence', session: headless }))).toEqual({ type: 'agent_presence', session: headless });
    for (const malformed of [{ platform: 'Windows' }, { capabilities: [123] }, { dependencies: 'win32' }, { profile: 'host' }, { architecture: 'x86' }]) {
      const target = { ...session.target!, execution_target: { ...session.target!.execution_target!, ...malformed } };
      expect(parseServerMessage(JSON.stringify({ type: 'agent_presence', session: { ...session, target } }))).toBeNull();
    }
  });
});
