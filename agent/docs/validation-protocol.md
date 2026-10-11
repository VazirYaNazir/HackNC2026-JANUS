# Generated C validation and backend repair

The agent implements the job contract, bounded repair state machine, synthetic
preflight interface, and separate execution authorization gate. Generation and LLM repair belong to the backend; no cloud LLM
API key is stored or used by the laptop agent.

JANUS ships `agent.container:create_boundary`, automatically selected by the
registered `connect` CLI unless a custom local adapter is configured. On macOS
and Windows it uses Docker Desktop's local Linux engine to compile and run
portable C. The physical OS still runs the five repository-owned native tools;
generated C targets the separate worker's Linux platform and architecture.
An unavailable or unverified worker blocks generated compilation and execution.
Neither a policy file nor a backend message can bypass that check.

## Prepare the included worker

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and use
its local Linux engine. `npm start` starts an existing installation if needed and
prepares the image after local startup consent. `npm run setup` also prepares it,
without authorizing or running generated programs. From the repository root:

```sh
uv run --locked --directory agent python -m agent.main worker status
uv run --locked --directory agent python -m agent.main worker prepare
```

`status` returns JSON with `available`, `reason` and `execution_target`.
`prepare` builds the repository's trusted image, then probes its actual kernel
restrictions; the first build may take two minutes. It does not install Docker
Desktop or grant execution consent. Missing Docker, a stopped daemon, a missing
image, a remote or Windows engine, unsupported architecture, or failed enforcement
checks keep the worker unavailable. Native controls remain usable with local and
OS approval. Fix the reported prerequisite and restart JANUS or prepare the worker
and reconnect the agent. There is no direct host-execution fallback.

A ready target advertises `platform: Linux`, `architecture: arm64` or `x86_64`,
`profile: portable-c`, supported capabilities and an empty dependency list. Standard
C uses the image's Linux compiler; macOS frameworks and Windows SDK libraries are
not supplied to generated programs.

| Included worker scope | Enforced behavior |
| --- | --- |
| `files.read`, `files.write` | Per-job temporary scratch files; no laptop directory or home/credential mount. Writes require the write grant and disappear when the job container is removed |
| `process.spawn` | Child processes inside the worker, within process, CPU and memory limits |
| `audio.volume`, `desktop.mouse` | Locally granted volume and mouse actions through checked native commands |
| `process.inspect` | Bounded native metrics and relevant-process snapshots |
| `desktop.screen` | Screen metadata when locally enabled; image frames remain on the opted-in native mirror stream |

The typed native broker supplies granted snapshots and checks a generated job's
JSON command list before applying any native effects. It exposes no arbitrary
shell, host path, credential or direct native API handle. Network access,
`external.send`, `admin`, `persistent.write` and arbitrary laptop file access are
unsupported. Advertising a capability or including it in a policy cannot add it
to the worker's enforced scope.

## Local policy

The local application chooses a grant for each unique job ID. A backend job
cannot create or expand that grant. Without a grant, validation returns
`REJECTED` / `AUTHORIZATION_REQUIRED`.

For CLI integration, create a local `.validation-policy.json` in the `agent/`
directory. That filename is ignored by Git because policies may contain private
runtime inputs. A minimal deterministic computation grant is:

```json
{
  "jobs": [{
    "job_id": "job-123",
    "operation": "compute",
    "capabilities": [],
    "dependencies": [],
    "side_effects": false,
    "execute_authorized": false,
    "share_outputs": false,
    "output_protocol": "text",
    "fixtures": [{"stdin": "2\n", "stdout": "4\n", "returncode": 0}],
    "execution_stdin": "",
    "max_attempts": 3,
    "max_source_bytes": 131072
  }]
}
```

Run from the repository root with this laptop's own `AGENT_TOKEN` in its local
environment:

```sh
uv run --locked --directory agent python -m agent.main connect \
  --url wss://your-backend/ws/agents/my-laptop --device-id my-laptop \
  --validation-policy .validation-policy.json
```

This registers grants and selects the included worker. The example permits
validation and synthetic preflight, but sets `execute_authorized` and output
sharing to false. Generated builds remain blocked when the worker is unavailable.
Unknown policy fields are rejected, including
worker commands and purported sandbox-verification flags. The policy file is
limited to 256 KiB and 256 explicit grants. Resource limits can be reduced by a
local Python application through `ExecutionPolicy(limits=ResourceLimits(...))`.

For approval once at local startup, a policy may instead contain `jobs: []` and a
`session` scope. [The complete example](../validation-policy.example.json) includes
all eleven capabilities, all supported dependencies, every operation (`["*"]`),
output sharing, three compilation attempts, 128 KiB source, and the existing
bounded worker budgets. The full launcher chooses only dependencies for the
current OS when preparing its default `.validation-policy.json`.

**A session JSON file never grants approval by itself.** Direct `connect` launches
must add `--approve-session`, disclose the local scope, and receive `APPROVE` from
an interactive terminal. Noninteractive input and any other answer stop startup.
The full local launcher asks once for its combined native/generated scope, then
starts the agent with `--session-approval-stdin` and sends exactly
`JANUS_SESSION_APPROVED\n` through its local stdin pipe. These two flags are
mutually exclusive. This is a local CLI facility: WebSocket messages cannot set
these flags, provide approval, or change the session scope. Approval exists only
in memory for the current agent process and is not persisted in the policy file.

An approved scope mints a separate immutable `ExecutionPolicy` for each fresh
request ID after parsing its full manifest. It preserves the operation, requested
capability/dependency subset, and `output_protocol`; names outside the local scope
are denied. Explicit per-job grants take precedence. Grant consumption, artifact
integrity, reconnection replay protection and the four-worker limit still apply.
Session consent cannot bypass missing worker prerequisites or unsupported scopes.
The session `limits` object can reduce the documented worker budgets but cannot
exceed their supported maxima. A narrowed scope may list specific `operations`
instead of `"*"`, and restrict `output_protocols` to `text` or `json-v1`.

`execute_authorized` defaults to false and must represent local authorization
for that particular job. `share_outputs` is a separate local opt-in to transfer
stdout, stderr, and the exit code to the backend. Both defaults are false.
`side_effects` defaults to true; only a locally classified pure computation with
no requested capabilities receives an automatic synthetic preflight.
`execution_stdin` is supplied locally at execution and never included in cloud
messages. Fixtures must be harmless synthetic data, not private runtime data.

Supported capability names are `files.read`, `files.write`, `network`,
`external.send`, `desktop.mouse`, `desktop.screen`, `audio.volume`,
`process.inspect`, `process.spawn`, `admin`, and `persistent.write`. Grant only
those required by the requested operation and advertised by the ready worker.
A declaration is not an OS permission or proof that a program follows the request.
The included worker supports only the scratch and broker scopes above. Custom
workers must enforce access to their specific locally granted paths, handles and APIs.

The following native dependency declarations remain part of the protocol for
explicit custom adapters targeting `Darwin` or `Windows`. They map to fixed local
compiler arguments and require the matching physical SDK/frameworks. They are
not dependencies available inside the included portable Linux worker:

| Dependency | Target | Libraries/frameworks |
| --- | --- | --- |
| `coreaudio` | macOS | CoreAudio, CoreFoundation |
| `applicationservices` | macOS | ApplicationServices |
| `iokit` | macOS | IOKit, CoreFoundation |
| `imageio` | macOS | ImageIO, CoreGraphics, CoreFoundation |
| `mac-system` | macOS | System headers; no additional library |
| `win32` | Windows | ole32, oleaut32, uuid, user32, gdi32, psapi, wbemuuid, windowscodecs |

## Backend messages

Use the existing authenticated agent WebSocket. The registered protocol version 2
`agent_hello` payload advertises the physical host platform and architecture,
attempt/worker limits, and build/run availability. A ready included worker adds
its separate `execution_target`; an unavailable worker reports `boundary_reason`.
Backend planning and generated-job validation use that execution target, while
built-in controls use the physical OS. Legacy validation messages retain their
version 1 contract inside the version 2 authenticated device envelope. Job events
are forwarded to connected Studio/PWA clients.

An initial job has exactly these fields:

```json
{
  "type": "validate_job",
  "job_id": "job-123",
  "attempt": 1,
  "language": "c11",
  "source": "#include <stdio.h>\nint main(void) { int x; if (scanf(\"%d\", &x) != 1) return 1; printf(\"%d\\n\", x * 2); return 0; }",
  "operation": "compute",
  "platform": "Linux",
  "architecture": "arm64",
  "capabilities": [],
  "dependencies": [],
  "output_protocol": "text"
}
```

Job IDs use 1–128 ASCII letters, digits, underscores, or hyphens. Operation names
use lowercase ASCII letters, digits, dots, underscores, or hyphens, begin with a
letter, and are at most 64 characters. Platforms are `Darwin`, `Windows` or `Linux`
and must match the locally configured execution target. The example targets an
arm64 Linux worker; use its advertised architecture. A `Darwin` or `Windows`
generated request requires an explicitly installed custom adapter for that target,
rather than inheriting access from the laptop's physical OS. Architecture aliases normalize `AMD64` to `x86_64` and
`aarch64` to `arm64`.
`output_protocol` is `text` or `json-v1`. Legacy messages may omit it, defaulting
to `text`; newly generated backend jobs retain their requested protocol.

Contract checks require nonempty UTF-8 source, no NULs, the local size limit,
exactly one normal `int main(void)` or `int main(int argc, char **argv)` definition,
and declared system headers/dependencies. Cloud compiler flags, include paths,
quoted private headers, and obvious undeclared shell/network/delete operations
are rejected. Obvious embedded absolute paths are also rejected; supply local
runtime inputs separately. These checks are deliberately conservative and can
reject otherwise valid C. They cannot detect every secret, hidden dependency,
side effect, or semantic mismatch. Actual isolation remains mandatory.

For a genuine compilation error, the agent sends:

```json
{
  "type": "repair_requested",
  "job_id": "job-123",
  "attempt": 1,
  "reason": "COMPILE_ERROR",
  "platform": "Linux",
  "architecture": "arm64",
  "compiler": "worker-gcc",
  "language_standard": "c11",
  "source_sha256": "<hash of failing source>",
  "diagnostics": "program.c:9:5: error: undeclared identifier"
}
```

Diagnostics contain only canonical error categories and `program.c` locations;
raw messages, symbol names, paths, snippets, and environment details are withheld.
The backend can use locations/categories together with source it already holds.
Unknown errors become `compiler error (detail withheld)`. Diagnostic text sent
to the backend is capped at 16 KiB. Source and local runtime input are not echoed.

Return exactly these fields for the next complete source revision:

```json
{
  "type": "code_revision",
  "job_id": "job-123",
  "attempt": 2,
  "language": "c11",
  "source": "/* complete revised standalone C11 source */"
}
```

Each revision undergoes the full source contract and a new build. The original
operation, target, dependencies, and capability scope remain fixed. Additional
revision fields are rejected. There are at most three total compilations and two
repair requests, with a 30-second revision deadline. Identical source, repeated
canonical diagnostics, stale IDs/attempts, malformed revisions, and timeouts end
the loop. Sanitization can classify different raw errors as the same diagnostic;
that conservative match stops further repair. Compiler/environment/resource or
runtime failures do not trigger automatic LLM repair.

The final validation event contains no binary, path, fixture, or runtime data:

```json
{
  "type": "validation_result",
  "job_id": "job-123",
  "status": "READY_FOR_EXECUTION",
  "reason": null,
  "attempts": 2,
  "compiler": "worker-gcc",
  "source_sha256": "<hash of final source>",
  "diagnostics": "",
  "preflight": "PREFLIGHT_PASSED"
}
```

Status is `REJECTED`, `VALIDATION_FAILED`, `VALIDATION_BLOCKED`, or
`READY_FOR_EXECUTION`. Reasons distinguish source/scope/platform problems,
compiler absence, build/run boundary absence, timeouts, resource limits, exhausted
attempts, repeated source/diagnostics, and invalid revisions. After a successful
repair, diagnostics may retain the last sanitized compilation error.

| Preflight | Meaning |
| --- | --- |
| `PREFLIGHT_PASSED` | Synthetic smoke/fixtures passed in the verified boundary. |
| `PREFLIGHT_LIMITED` | Side effects/capabilities prevent a meaningful pre-run; no native pre-run occurred. |
| `PREFLIGHT_FAILED` | Loading, timeout, limits, exit status, framing, or fixture output failed. |
| `PREFLIGHT_BLOCKED` | Validation cannot safely reach preflight; no automatic launch. |

For pure computation, preflight denies all capabilities, uses local synthetic
stdin, checks the exit code and framing, and compares exact stdout when fixtures
exist. `json-v1` framing means one stdout JSON object with integer `version: 1`;
otherwise output is bounded UTF-8 text. No fixtures means a smoke test with empty
stdin and expected exit code zero. Side-effectful jobs skip pre-run, rather than
exercising real permissions. Missing run isolation blocks readiness and discards
the compiled artifact.

Execution is a distinct request, accepted only for a ready result in the current
connection, its unchanged artifact, and its existing local authorization:

```json
{"type":"execute_job","job_id":"job-123","source_sha256":"<hash of final source>"}
```

The agent verifies the executable hash, requires the granted capabilities to be
supported by the boundary, and consumes authorization once. It returns an
`execution_result` with `job_id`, `status`, and `reason`. Status is `COMPLETED`,
`EXECUTION_FAILED`, or `EXECUTION_BLOCKED`. Only local `share_outputs: true`
includes stdout/stderr/returncode. Remote authorization flags, policy fields,
runtime inputs, executable paths, and output-sharing flags are rejected.

```json
{"type":"cancel_job","job_id":"job-123"}
```

Cancellation stops the pending job and invalidates its artifact. Disconnect
cancels every validation, repair, and execution task and deletes build artifacts.
A revision without a pending repair is rejected. Job IDs cannot be reused across
reconnections in the same agent process. The replay ledger allows 256 IDs before
a local restart; it is not a durable ledger across process restarts. Local apps
must not reissue consumed authorizations after restart. The service retains at
most four distinct jobs/artifacts per connection. A shared worker pool allows up
to four concurrent build/run operations; a local app can lower this through
`ValidationService(..., max_parallel=1..4)`. Waiting for a backend revision holds
no worker slot, so another job can progress. Build/run deadlines include time
waiting for a slot. Artifact hashing uses threads and does not block the receiver.
Disconnect invalidates ready artifacts immediately and waits for hash readers
before removing their files, including handles that Windows cannot delete while
open.
The WebSocket receiver keeps handling telemetry, existing controls, and ping/add
jobs while validation waits. Inbound frames are capped at 1 MiB to accommodate
JSON escaping; decoded source remains capped at 128 KiB.

## Custom local workers

`IsolationBoundary` in `agent/isolation.py` defines async `compile` and `run`
methods returning `CompileResult` and `ProcessResult`. A trusted local app passes
its audited adapter to `ValidationService(compiler, policies, boundary=worker)`.
The registered CLI selects the included Docker worker automatically. Setting
`AGENT_ISOLATION_ADAPTER=your_local_module:create_boundary` or `--isolation-adapter`
replaces it with an explicitly installed local factory. A module setting alone
is not an installation or an enforcement audit. Protocol unit tests use a simulated
adapter that never executes generated code; it must not be used in production.

An adapter may advertise build/run guarantees only after verifying OS enforcement
of filesystem, credentials, network, CPU, memory, subprocess, disk, deadline, and
output restrictions. It must use a clean environment, deny home/credentials and
network by default, allow only required compiler/SDK inputs and local job files,
and expose only locally granted runtime paths/handles/APIs. A runtime adapter
must reject capabilities it cannot safely enforce. A capability name alone does
not authorize unrestricted filesystem, network, or desktop access.
Compile/run methods must yield to the event loop while the native worker operates;
blocking OS calls belong in worker processes or threads. The adapter must support
concurrent calls with separate job directories and enforce any ordering required
by conflicting granted resources. The worker pool bounds concurrency but does
not provide OS isolation.

Defaults are 30 seconds per build, 3 seconds per run, 64 KiB per compiler output
stream, 64 KiB runtime stdout, 16 KiB runtime stderr, 512 MiB memory, 64 MiB disk,
16 processes, and 30 CPU seconds. Enforce these inside the OS worker, including
descendants; Python deadlines and output checks provide additional guards. On
timeout, cancellation, or disconnect the adapter must kill and reap its entire
worker tree before returning. It must protect the artifact against replacement
between hash verification and execution.
Trusted container creation has a separate 20-second watchdog allowance before
the guest starts. It does not extend the guest's 30-second compile or 3-second
run deadlines, CPU budget or other resource caps. Cancellation waits for a pending
create transaction to finish before removing its container, preventing late orphans.
These are per-worker budgets: four concurrent workers can require up to 2 GiB of
memory and 64 worker processes. Choose a smaller local parallel limit or smaller
resource budgets for machines that cannot provide that capacity.

`CCompiler.compile_detailed` and `run_bounded` are used for trusted repository
sources/compiler probes. They cap capture/time and return detailed local results,
but provide no native isolation and must not serve as the generated-code worker.
Compiler success requires both a zero exit status and the expected executable.
The Zig fallback uses pinned official archive SHA-256 hashes and a cached receipt
covering the compiler and support files; altered caches are refused. This assumes
the app-managed receipt and local toolchain installation are trusted. No package
installation or repair permission is taken from a cloud message.

## Verification

Run `uv run --locked python -m unittest discover -s tests -v` from `agent/`.
Protocol tests cover bounded repair, scope/revision rejection, diagnostic privacy,
missing boundaries, fixtures, artifact tampering, single-use execution, local
output opt-in, timeout/resource handling, and disconnect cleanup. A real local
WebSocket test handles ping while awaiting a repair. A trusted author-written
C fixture tests structured compiler results without executing generated code.
Native SDK builds run only on macOS/Windows and are skipped on Linux. Normal
discovery skips the real Docker tests unless explicitly enabled. Prepare the image,
then run from the repository root:

```sh
JANUS_TEST_CONTAINER=1 uv run --locked --directory agent python -m unittest tests.test_container_integration -v
```

These 17 tests exercise real compilation/execution, host path/header and credential
isolation, scratch write grants and freshness, network denial, process grants and
limits, detached-child cleanup, independent output caps, memory/disk/CPU budgets,
wall deadlines, cancellation during execution and container creation, and parallel job separation. Opting in with an
unavailable boundary fails instead of reporting containment as skipped. Native
permissions and Docker Desktop installation/startup still need verification on
the intended macOS/Windows laptop; Linux engine results do not establish those.
For actual Gemini generation and separately enabled repair checks, see
[complete application testing](../../docs/testing.md#real-gemini-and-worker-integration).
See [the detailed test matrix](testing.md) for per-module coverage and concurrency
regressions.
