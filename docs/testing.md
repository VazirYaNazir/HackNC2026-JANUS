# Test the complete application

From the repository root, install Node 22+, `uv`, a C compiler, and the locked dependencies:

```sh
npm ci
uv sync --locked --directory agent
uv sync --locked --directory backend
npx playwright install chromium
```

On a Linux CI machine, use `npx playwright install --with-deps chromium` to install browser system libraries too. macOS native builds need Xcode Command Line Tools; Windows native builds use MSVC with its SDK, MinGW-w64 or the agent's verified Zig fallback. Docker is optional for ordinary unit/browser tests. Real generated-program containment checks require a running local Linux Docker engine with cgroup v2; [Docker Desktop](https://www.docker.com/products/docker-desktop/) supplies that engine on macOS and Windows.

```sh
npm test                  # all unit suites, coverage gates, lint, both builds, browser workflows
npm run test:unit         # all Python and TypeScript unit suites
npm run test:coverage     # full source coverage and HTML reports
npm run test:e2e          # lint/build both apps, then run browser workflows
```

Coverage reports are written to `coverage/index.html`, `agent/htmlcov/index.html`, and `backend/htmlcov/index.html`. Python coverage measures statements and branches, with existing gates of 99% for the complete agent package and 95% for the complete backend app. JavaScript reports include both apps and all shared packages, including unvisited files, with gates of 80% statements, 70% branches, 75% functions and 85% lines; they do not claim 100% coverage. Generated reports and browser traces are ignored by Git.

Unit tests cover all widget families, ranges and thresholds, input/edit modes, formatting, charts and logs, process hierarchy and cycle handling, screen errors, project changes and undo/redo, import/export validation, connection and pairing forms, saved phone credentials, install prompts, the demo client, live subscriptions and stale data, reuse approval, native queues and StrictMode cleanup. The Python suites cover standalone C harnesses, compiler/cache behavior, validation and repair, worker concurrency and cancellation, protocol framing, native process startup/shutdown, per-device controls, Gemini/provider behavior, SQLite operations, authenticated routes, fanout, legacy/admin APIs, and tunnel lifecycle.

Browser tests use production builds, a real HTTP/WebSocket backend, real temporary SQLite databases, and the actual QR decoder with a test camera. The generated-program fixture compiles and executes only exact author-written C examples; it is not a production isolation adapter. Portable-worker browser fixtures check Linux planning, minimum-access selection, source review, explicit execution, fresh IDs and unchanged panel bindings. These fixtures do not attest kernel isolation. Native telemetry and commands use a deterministic fixture rather than controlling a real desktop. A browser-generated JPEG passes through the agent protocol, backend, and both real screen widgets.

## Real worker enforcement

JANUS ships `agent.container:create_boundary` and selects it automatically for registered agents. `npm start` prepares its image after local startup consent and starts an existing Docker Desktop installation when needed; `npm run setup` also prepares it. From the repository root, these commands inspect readiness and prepare the trusted image without authorizing any generated program:

```sh
uv run --locked --directory agent python -m agent.main worker status
uv run --locked --directory agent python -m agent.main worker prepare
```

The JSON report must show `available: true`. A Docker process or image tag alone is insufficient: the factory probes actual network, filesystem and resource enforcement. Missing prerequisites leave the worker unavailable while locally authorized native controls remain usable. `status` does not build the image; `prepare` does not install Docker Desktop.

The physical laptop remains macOS/Windows for native controls; generated C targets the worker's `Linux` platform, `arm64` or `x86_64` architecture and `portable-c` profile. It provides standard C with no macOS frameworks, Windows SDK or additional dependency libraries. Granted file operations use disposable per-job scratch space, and granted child processes remain inside the bounded worker. The typed native broker exposes only checked volume/mouse commands and bounded metrics/process/screen-metadata snapshots. Screen frames use the separate locally enabled mirror stream. Network, external sending, administrator privileges, persistent host changes and arbitrary laptop files are unsupported, including when a policy contains those names.

Trusted container creation has a separate 20-second watchdog allowance. Guest compile/run budgets remain 30/3 seconds with their existing CPU/resource caps; the creation allowance does not extend guest execution. Cancellation waits for creation to finish before removing the container. Readiness commands allow 60 seconds for a probe and 240 seconds for preparation, including Docker/image overhead.

Ordinary unit discovery skips `tests.test_container_integration` unless opted in. After preparation, run its 17 real enforcement tests with a POSIX shell:

```sh
JANUS_TEST_CONTAINER=1 uv run --locked --directory agent python -m unittest tests.test_container_integration -v
```

On Windows PowerShell, set `$env:JANUS_TEST_CONTAINER = '1'` and run the same `uv` command. Remove the variable afterwards with `Remove-Item Env:JANUS_TEST_CONTAINER` when returning to ordinary tests.

These tests exercise real isolated compilation/execution, inaccessible private host files and compiler headers, absent credentials, scratch write grants and freshness, network denial, process grants/caps, detached-child cleanup, independent stdout/stderr caps, memory/disk/CPU limits, wall deadlines, cancellation during execution and container creation, and concurrent job separation. They verify that each owned container is gone before the test ends. With opt-in enabled, an unavailable worker fails the suite; it does not silently skip containment checks.

## Real Gemini and worker integration

This separate backend suite makes actual Gemini calls and uses the shipped Docker worker, a real authenticated HTTP/WebSocket connection and a temporary SQLite database. It requests pure computation and no desktop effects. It requires an existing `GEMINI_API_KEY` or `GOOGLE_API_KEY` in the environment or preserved `backend/.env`, plus the prepared, ready worker:

```sh
JANUS_RUN_LIVE_WORKER=1 uv run --locked --directory backend python -m unittest tests.test_live_worker.LiveWorkerTests -v
```

The test plans and generates portable source, reviews its exact scope, supplies a fresh local grant, then checks real compilation, synthetic preflight, execution, output and replay rejection. Normal test runs skip live provider calls. If opted in without a usable key or worker, this live suite reports a skip; that does not establish live generation/execution readiness.

Real compile-error repair spends an additional provider call and needs a separate opt-in alongside the first:

```sh
JANUS_RUN_LIVE_WORKER=1 JANUS_RUN_LIVE_WORKER_REPAIR=1 uv run --locked --directory backend python -m unittest tests.test_live_worker.LiveWorkerTests -v
```

PowerShell uses `$env:JANUS_RUN_LIVE_WORKER = '1'` and, for repair, `$env:JANUS_RUN_LIVE_WORKER_REPAIR = '1'` before the `uv` command. Remove those variables after testing. Keep passed, failed and skipped outcomes distinct; mocked Gemini and simulated boundaries cover protocol behavior separately.

## Native laptop and phone verification

GitHub Actions runs the application integration suite and a separate actual Linux worker-enforcement job, and builds all five standalone C tools against macOS and Windows SDKs. Linux containment results do not prove Docker Desktop installation/startup or native permissions on a Mac/Windows laptop. OS build checks also do not prove that audio devices, mouse permissions, camera permissions, screen-recording permissions or CPU temperature hardware are available. Verify those on the intended laptop and phone:

1. Start the registered agent with `--native-tools`, adding `--screen` only when screen sharing is intended.
2. Create **Project → Laptop controls starter** in Studio and **Save & Push**.
3. Pair the phone with the actual HTTPS backend through QR scanning or manual code entry.
4. Adjust volume, hold and release the joystick, switch away from the phone app, and verify that mouse movement stops.
5. Check RAM, available CPU temperature, relevant process ancestors, and the actual screen. Apple Silicon temperature may need the documented local sensor file.
6. Stop/restart the agent; readings should disappear when offline and resume when it reconnects.
