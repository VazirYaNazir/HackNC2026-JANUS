import type { GenerationRequest } from '@janus/schema';
import type { Session } from './api';

/** Generated programs target the worker; native controls continue targeting the laptop. */
export function generationTarget(session: Session | null) {
  return session?.target?.execution_target ?? session?.target ?? null;
}

/** A review can request execution only when its exact target and scope are supported now. */
export function executionUnavailable(session: Session | null, request?: GenerationRequest): string | null {
  if (!session?.online || !session.target) return 'Connect your laptop agent to request execution.';
  if (!session.target.build_boundary_available || !session.target.execution_boundary_available) {
    const reason = session.target.boundary_reason;
    if (reason === 'DOCKER_NOT_INSTALLED') return 'Install and start Docker Desktop, then restart JANUS to prepare the isolated worker.';
    if (reason === 'DOCKER_NOT_RUNNING' || reason === 'DOCKER_UNAVAILABLE' || reason === 'DOCKER_DAEMON_UNAVAILABLE') return 'Start Docker Desktop, then restart JANUS to prepare the isolated worker.';
    if (reason === 'DOCKER_LINUX_ENGINE_REQUIRED') return 'Switch Docker Desktop to Linux containers, then restart JANUS.';
    if (reason === 'DOCKER_LOCAL_ENGINE_REQUIRED') return 'Select the local Docker Desktop engine, then restart JANUS.';
    if (reason === 'WORKER_ARCHITECTURE_UNSUPPORTED') return 'The worker architecture is unsupported. An arm64 or x86_64 Linux worker is required.';
    return `Execution is unavailable on this laptop. Start Docker Desktop and restart JANUS to prepare the isolated worker.${reason ? ` Worker status: ${reason}.` : ''}`;
  }
  if (request) {
    const target = generationTarget(session)!;
    if (request.platform !== target.platform || request.architecture !== target.architecture) return 'This source targets a different system. Generate it again for the current worker before running it.';
    const worker = session.target.execution_target;
    if (worker) {
      const missing = request.capabilities.filter(capability => !worker.capabilities.includes(capability));
      if (missing.length) return `The worker does not support the requested access: ${missing.join(', ')}. Generate with supported capabilities.`;
      const libraries = request.dependencies.filter(dependency => !worker.dependencies.includes(dependency));
      if (libraries.length) return `The portable worker does not provide these libraries: ${libraries.join(', ')}. Generate portable C source.`;
    }
  }
  return null;
}
