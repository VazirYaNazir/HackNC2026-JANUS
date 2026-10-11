# Backend–agent jobs and Gemini

This extension adds a device-specific, authenticated job channel and backend
generation services. It uses the existing agent `ValidationService`, local
policies, compiler, preflight, artifact verification, and single-use execution.
The desktop Studio and PWA now use this channel through scoped panel controls,
pairing, and SQLite persistence. See [connected panels](connected-panels.md) for
the full frontend workflow. Legacy agent and Cloudflare configuration remain
available; public mobile controls route to the laptop agent.

## Configuration and startup

From `backend/`, install Python 3.12+ dependencies with `uv sync --locked`.
Runtime additions are Google's supported `google-genai` SDK, `httpx`, and an
explicit `python-dotenv` dependency. Development adds `coverage`; the lockfile
pins the actual versions. The Dockerfile name now matches Compose's `Dockerfile`
reference on case-sensitive systems. `.dockerignore` excludes local environment
files, data, databases, virtual environments, and tests from the image.

Keep credentials in environment settings or the ignored `backend/.env`. Register
laptops through `AGENT_DEVICES_JSON`; Studio pairs phones to these laptops:

```json
[
  {
    "device_id": "laptop-mac",
    "owner_id": "alice",
    "agent_token": "<distinct random credential, at least 32 characters>",
    "controller_token": "<another random credential, at least 32 characters>"
  }
]
```

Replace the placeholders with distinct cryptographically random tokens, for
example values produced by `secrets.token_urlsafe(32)`. Device IDs and both
roles' credentials must be unique; one owner can register multiple laptops.
Neither credential can be reused for another registered device or role. The backend retains only
credential hashes in its registry, compares them with constant-time comparison,
and never accepts owner IDs or credentials from job bodies. A controller can
access only its registered laptop's jobs, including status/wait/cancel requests.

Alternatively, `AGENT_DEVICES_FILE` names a local UTF-8 JSON file with the same
list and takes precedence over JSON in the environment. Keep it outside source
control or in ignored `backend/data/`, restrict its permissions, and mount it
into Docker when needed. With the existing `backend:/app` Compose mount, a file
in `backend/data/agent-devices.json` is `/app/data/agent-devices.json` in Docker.
There are at most 128 registrations and 64 KiB of configuration. Invalid
configuration stops startup with a generic error without echoing credentials.
Registrations import into SQLite at startup, and database registrations also
load when environment entries are absent. Restart to apply explicitly imported
credential rotations. Removing an environment entry alone does not revoke a
persisted registration. Existing connections and jobs terminate on shutdown.

| Variable | Default / purpose |
| --- | --- |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Credential for live generation; Gemini-specific key takes precedence. Never included in requests to agents. |
| `GEMINI_MODEL` | `gemini-3.8-flash`; choose another supported model through configuration. Google's [current model guidance](https://ai.google.dev/gemini-api/docs/deprecations) recommends current models for new projects and limits 2.5 access to existing users. |
| `GEMINI_MAX_CONCURRENT` | 8; local range 1–32 simultaneous provider calls. |
| `GEMINI_TIMEOUT` | 20 seconds total per generation/repair, including queue wait and API retries; maximum 25. |
| `GEMINI_API_ATTEMPTS` | 2; range 1–3 attempts, only rate limits and server errors retry. SDK automatic retries are disabled. |
| `AGENT_MAX_ACTIVE_JOBS` | 16 active jobs globally. |
| `AGENT_MAX_JOBS_PER_DEVICE` | 4 outstanding jobs, local range 1–4; distinct from native worker count. |
| `AGENT_JOB_TIMEOUT` | 150 seconds total, including generation, validation, repair, and execution. |
| `APP_ENV` | `development` allows loopback `ws`; every other value requires `wss` on the new agent endpoint. |
| `PUBLIC_BACKEND_URL` | Optional stable HTTPS backend origin advertised for phone pairing; otherwise the current HTTPS request or automatic Cloudflare tunnel is used. |
| `PWA_PUBLIC_URL` | Optional deployed phone app HTTPS origin; Studio's QR opens it with a pairing review form. |
| `JANUS_DB_PATH` | Optional path in an existing writable directory; default `app/app.db`. Persist it and its WAL sidecars outside standalone containers. |

From the repository root, use the existing Docker workflow:

```sh
cp backend/.env.example backend/.env  # only if no existing .env is present
docker compose up --build -d backend
```

Configure actual registrations and Gemini credentials in the environment file
before live use. Health remains `GET /health`. The new job routes are mounted
on the public backend's event loop, not the existing admin server's separate
thread. Run one Uvicorn process: active connection routing is in memory. Running
multiple worker processes without shared routing state is not supported.
SQLite preserves scoped job views, replay IDs, layouts, action templates,
paired phones, and validated programs. Restart marks unfinished jobs
`INTERRUPTED / BACKEND_RESTART`; neither reconnect nor restart replays a job.

Deployment uses WSS and preserves certificate verification. Configure the
existing TLS terminator/reverse proxy to pass the scheme through Uvicorn's
trusted proxy settings. The agent rejects remote plaintext WebSocket URLs and
credentials in URLs. WebSocket frames are checked against a 1 MiB application
limit; deployment can additionally set Uvicorn `--ws-max-size 1048576`.

## Local agent initialization

Run through the existing entrypoint from `agent/` on macOS or Windows:

```sh
uv sync --locked
uv run --locked python -m agent.main connect \
  --url wss://backend.example/ws/agents/laptop-mac \
  --device-id laptop-mac \
  --workers 4 \
  --validation-policy /path/to/local-grants.json
```

Set `AGENT_TOKEN` to that laptop's agent credential in its local environment.
`AGENT_BACKEND_URL`, `AGENT_DEVICE_ID`, and `AGENT_ISOLATION_ADAPTER` can supply
defaults. The `connect` command carries only generated-job events; it does not
start screen, process-tree, mouse, volume, or other telemetry uploads. The
existing `stream` command remains the separate telemetry/control workflow.

The existing local grants are keyed by job ID. Choose a fresh ID locally, grant
only the intended operation/capabilities, and submit that same ID through the
control API. Grants, fixture input, real runtime input, paths, side-effect and
execution authorization, and output sharing remain local. See the existing
[agent validation protocol](../../../agent/docs/validation-protocol.md).

For a locally installed and audited worker implementation, explicitly select
`--isolation-adapter my_local_app.worker:create_boundary`. The local no-argument
factory must return the existing `IsolationBoundary` interface, advertise all
required build/run guarantees, and implement asynchronous `compile` and `run`.
Its blocking OS operations must use bounded workers and it must kill/reap its
worker tree before returning from cancellation/timeout. Interface checks do
not audit or prove OS containment. This option is trusted local configuration;
there is no network message or remote policy field that can select an adapter.
No macOS/Windows isolation implementation is supplied by this change. Without
one, generation/communication work and local validation reports
`VALIDATION_BLOCKED / BUILD_BOUNDARY_UNAVAILABLE`; generated source is never
passed to a compiler. An absent local grant reports `AUTHORIZATION_REQUIRED`.

## Control endpoints

HTTP job endpoints accept the registered controller credential or a paired
phone's scoped credential through `Authorization: Bearer <token>`. Studio-only
mutation/pairing endpoints require the controller role. Job submission returns
HTTP 202 after durable admission and does not occupy a request while Gemini or
native work runs. Both frontends use the prepared panel action endpoints
described in [connected panels](connected-panels.md).

| Method / path | Behavior |
| --- | --- |
| `WS /ws/agents/{device_id}` | Outbound laptop connection, authenticated with that device's agent token. |
| `POST /api/v1/agent-jobs/generate` | Structured request -> Gemini -> local validation/repair -> execution request. |
| `POST /api/v1/agent-jobs/source` | Already generated source through the same local pipeline; no initial provider call. Compile repair can still invoke Gemini. |
| `GET /api/v1/agent-jobs` | Recent persisted history scoped to the authenticated laptop and owner. |
| `GET /api/v1/agent-jobs/{job_id}` | Current state/result for the authenticated controller. |
| `GET /api/v1/agent-jobs/{job_id}/wait?timeout=30` | Async event-driven wait, 0 < timeout <= 180 seconds. Wait timeout is HTTP 504 and leaves the job running. |
| `POST /api/v1/agent-jobs/{job_id}/cancel` | Cancel generation/repair and request local cleanup. `cancellation_confirmed` becomes true only after the agent reports completed cleanup. |

Generation body:

```json
{
  "device_id": "laptop-mac",
  "job_id": "locally-granted-unique-id",
  "request": {
    "description": "Print a JSON object with version 1 and value 42",
    "operation": "compute",
    "platform": "Darwin",
    "architecture": "arm64",
    "capabilities": [],
    "dependencies": [],
    "output_protocol": "json-v1"
  }
}
```

Targets are `Darwin` or `Windows`; architectures are `arm64`, `x86_64`, or `x86`.
The connected laptop's advertised target must match. Capabilities/dependencies
use the existing agent vocabulary and declared dependencies must support the
target. Gemini returns strictly validated `language: c11`, source (at most
128 KiB UTF-8), identical operation/target/declarations/output protocol, and a
bounded summary. The `/source` body additionally supplies that `program` object.
Metadata cannot broaden the original request. Only the source manifest is sent
to the agent; output-protocol authorization still comes from local policy.

Status views contain `job_id`, `device_id`, `state`, `reason`, `repairs`,
`acknowledgments`, `source_sha256`, `result`, `cancellation_confirmed`, and
`elapsed_ms`. Terminal states distinguish validation rejection/failure/blocking,
execution failure/blocking, completion, provider failure, cancellation, total
deadline, and disconnection. Neither source nor executable paths are echoed in
these views. Runtime stdout/stderr/exit status appear only if the local grant
explicitly enables `share_outputs`; absent output is preserved as absent.

Errors: 401 invalid credentials, 403 wrong device scope, 404 inaccessible/missing
job, 409 duplicate ID, 422 malformed request/target, 429 active job capacity,
503 offline device/service capacity, and 504 wait timeout. Submission to an
offline device makes no provider call. Duplicate IDs never regenerate source.
At most 256 active/recent views remain in memory, evicting oldest records whose
cleanup has finished. Production's SQLite admission ledger rejects duplicate
IDs across restarts; the memory ownership cache stays bounded to 4096 IDs and
scoped database lookup handles late evicted results. Store-free test runtimes
reject new IDs once their lifetime 4096-ID ledger fills. Cleanup and pending
database admissions count toward active capacity. Restart/local authorization
recovery must not reissue a previously consumed job grant.

## Versioned WebSocket protocol

Every text JSON envelope contains exactly these fields:

```json
{
  "version": 2,
  "type": "validate_job",
  "device_id": "laptop-mac",
  "job_id": "locally-granted-unique-id",
  "state": "VALIDATING",
  "payload": {
    "attempt": 1,
    "language": "c11",
    "source": "int main(void) { return 0; }",
    "operation": "compute",
    "platform": "Darwin",
    "architecture": "arm64",
    "capabilities": [],
    "dependencies": []
  }
}
```

The transport converts payloads directly to the existing local manifest/event
interfaces by restoring `type` and `job_id`. No local permission fields, paths,
artifact uploads, runtime input, or file contents are introduced.

| Type | State | Payload |
| --- | --- | --- |
| `agent_hello` | `CONNECTED` | Platform, normalized architecture, worker count, max attempts, boundary availability; null job ID. |
| `connection_ready` | `CONNECTED` | Backend heartbeat interval; null job ID. |
| `ping` / `pong` | `CONNECTED` | Matching nonce; null job ID. |
| `validate_job` | `VALIDATING` | Existing initial manifest minus outer type/ID. |
| `job_ack` | `ACCEPTED` | `accepted_type`; receipt/routing acknowledgement, not permission to execute. |
| `repair_requested` | `REPAIRING` | Existing sanitized compile diagnostics, failing source hash, attempt, and compiler/target metadata. |
| `code_revision` | `REPAIRING` | Next attempt, C11, complete replacement source; original scope stays fixed. |
| `validation_result` | Local validation status | Existing bounded validation result. |
| `execute_job` | `EXECUTING` | Validated final source hash; local authorization remains mandatory. |
| `execution_result` | Local execution status | Status/reason, optionally locally authorized output. |
| `cancel_job` | `CANCELLED` | Empty payload. Local cleanup completion returns a cancellation validation result. |

Binary, oversize, malformed envelopes, unknown versions, and incorrectly routed
frames terminate the connection. Active job events also enforce their payload
schema and expected state; known stale results are discarded after an ownership
check. An agent cannot deliver
another connection's job results by guessing an ID. One registered connection
per laptop prevents ambiguous routing; duplicates do not replace the original.

Network operations use asyncio, one frame writer lock per backend connection,
and one ordered agent sender. Application heartbeats are sent every 10 seconds
with a 20-second reply deadline; the agent also uses native WebSocket ping/pong
at 20-second intervals/deadlines. Reconnection has exponential backoff capped
at 30 seconds with uniform 50–100% jitter and resets after the authenticated
handshake. Disconnect cancels affected jobs, invalidates artifacts, and never
replays them on the next connection. Repeated shutdown cancellation still waits
for local cleanup. Cancellation delivery without a cleanup reply remains
unconfirmed; a disconnected job's execution outcome can be unknown.

## Generation, repair, and concurrency

`LLMProvider` separates generation from transport and orchestration;
`GeminiProvider` uses `client.aio.models.generate_content` with JSON schema output.
Calls are semaphore-bounded and have an overall deadline including waiting,
rate-limit/server retry delays, and network time. Authentication/client errors,
malformed JSON/schema/scope, transport failures, and deadline expiry produce
sanitized reasons. Provider response bodies and credentials are never returned
in errors. The SDK request and retry paths are tested with deterministic mocks.

Only canonical agent diagnostics can reach Gemini: known error categories with
`program.c` line/column or a `compiler` label, capped at 16 KiB. Backend-held
source accompanies the correction request; user paths, symbol names, snippets,
local fixtures, inputs, and raw compiler output are withheld. The initial
generation plus at most two source repairs gives at most three compilations.
Attempts, source hashes, target, schema, and original metadata are correlated.
Repeated source, stale/duplicate repairs, exhausted attempts, provider failure,
or local timeout stops the workflow. Runtime and infrastructure failures never
trigger code repair.

Jobs wait on connection events rather than polling or repeatedly calling Gemini.
Generation/repair never occupies the WebSocket receiver. Each laptop shares its
existing 1–4 native compile/run slots across jobs; backend revision waits release
those slots. The network remains responsive during blocked compile/run workers.
Dependent stages within one job and single-use artifact claims stay ordered.
All native resource restrictions remain enforced by the selected local adapter
and local policy, independently of backend concurrency limits.

## Tests and measured results

From `backend/`:

```sh
uv run --locked coverage run -m unittest discover -s tests
uv run --locked coverage report -m
```

Coverage targets `app.agent_runtime`, job/Studio/socket/panel routes, panel
models, and `app.db`, with a 95% statement/branch gate. Existing Cloudflare and
legacy admin/agent modules are outside this target. From `agent/`, run the same
commands; its entire Python package keeps the 99% gate. No coverage exclusions
are added. Frontend builds, transport/editor unit tests, and a production browser
test are documented in [connected panels](connected-panels.md#verification).

Backend tests establish real loopback Uvicorn/WebSocket/HTTP connections and use
the actual agent transport, `ValidationService`, source contract, preflight,
hashing, local authorization, and executor. The test-only adapter accepts only
an exact allowlist of author-written C fixtures, invokes the existing compiler
and bounded subprocess helper in threads, and actually runs those fixture
binaries. It mocks the isolation guarantee flags for this trusted test; it does
not prove OS containment and cannot run arbitrary or live-generated C.

Tests cover authentication and separate credential roles, device/owner routing,
source/schema/provider failures, simultaneous agents/jobs, heartbeat liveness
during compilation, bounded retries/repair, privacy, capacity, replay/retention,
timeouts, cancellation confirmation, disconnect/reconnect, malformed/binary
frames, duplicate connections, shutdown races, and existing route/lifecycle
preservation. A benchmark test writes `/tmp/janus-pipeline-benchmark.json` with
eight-job/two-device wall time, median/max per-job latency, peak compile workers,
and provider request count. Each native compile includes an intentional 50 ms
fixture delay to demonstrate overlap. These are local plumbing measurements
with mocked provider latency, not cloud or live Gemini performance.

Verified in this Linux cloud environment with the locked dependencies:

| Suite | Tests | Passed | Skipped | Combined statement/branch coverage |
| --- | ---: | ---: | ---: | ---: |
| Entire agent Python package | 210 | 209 | 1 native platform SDK check | 99.84% |
| Backend jobs, database, connected panels | 86 | 85 | 1 optional live Gemini test | 97.10% |
| Frontend editor/shared client | 26 | 26 | 0 | Not measured |
| Production Studio + PWA browser workflow | 1 | 1 | 0 | Not measured |

The measured eight-job run used two authenticated devices belonging to different
owners. It finished in 427.044 ms; median per-job latency was 342.459 ms and
maximum latency was 378.876 ms. Each device reached four simultaneous compile
workers, and the mocked Gemini service received exactly eight initial generation
requests. The trusted fixture binaries were compiled and executed locally.
These values are a single coverage-instrumented loopback run, not a performance
guarantee; the benchmark reports fresh values on each suite run.

The backend image also built and started through Docker Compose. Container
checks verified `/health`, registration of the new OpenAPI routes, HTTP 401 for
unauthorized job access, and WebSocket rejection for unauthorized agents. Image
inspection confirmed environment files, test adapters, and the build CA bundle
were absent from the standalone image. This cloud's restricted container network
required a temporary Compose/build override using its existing proxy DNS and a
read-only public CA bundle; certificate verification remained enabled. That
override lives outside the checkout and is reproduced by the saved environment
setup draft. Repository Cloudflare configuration was unchanged; both frontend
applications now connect through the documented shared client.

The live provider test is opt-in and performs generation/schema validation only:

```sh
JANUS_RUN_LIVE_GEMINI=1 uv run --locked python -m unittest tests.test_llm.LiveGeminiTest -v
```

It additionally needs an environment-provided Gemini credential and may incur
provider usage. It never compiles or runs the returned code. Target macOS/Windows
SDK/hardware and real isolation containment require their existing integration
checks; the Linux fixture pipeline is not a substitute for those checks.
