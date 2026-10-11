# Agent tests and concurrency

All code and tests remain under `agent/`. The suite tests every Python agent
module and the portable logic of all five native C programs. It includes unit
tests plus real local subprocess/WebSocket tests. Platform branches use explicit
mock fixtures where the host cannot run the target API.

## Run on macOS, Windows, or Linux

From `agent/`, use Python 3.12+ and the locked development dependencies:

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked coverage run -m unittest discover -s tests
uv run --locked coverage report -m
```

`coverage report` fails below 99% combined Python statement/branch coverage. No
application modules or branches are excluded to manufacture the percentage.
Coverage includes threaded builds, capture threads, and artifact verification.
To inspect individual lines locally:

```sh
uv run --locked coverage html
```

Open `htmlcov/index.html`. Reports are ignored by Git. Standard unit test
discovery also works without coverage installed.

Linux needs a host C compiler (`cc`) for author-written native test harnesses.
macOS needs Xcode Command Line Tools and its SDK/frameworks. Windows needs MSVC
and the Windows SDK, MinGW-w64, or the agent's verified Zig fallback. Native
harnesses compile with C11 and the same target libraries as production tools.
No generated native source is compiled or executed by validation tests.

## Python module matrix

| Module | Test files | Covered behavior |
| --- | --- | --- |
| `main.py` | `test_main.py` | Every command, module entrypoint, environment defaults, argument forwarding without a shell, local policy setup, missing token, invalid options/policy, compiler/OS errors, Ctrl+C exit status, credential privacy. |
| `compiler.py` | `test_compiler.py`, `test_validation.py` | Clang/GCC/Zig/MSVC C11 argument lists, spaced paths, object/link flags, detailed results, stale output deletion, missing artifact, timeout/output/cancellation failures, compiler preference/probes, missing/broken system compiler, fallback and unsupported OS. |
| `installer.py` | `test_installer.py`, `test_validation.py` | All supported release/platform combinations with real ZIP/tar fixtures, checksum rejection, download size limit, corrupt/missing payloads, network failure, path traversal, warm/legacy cache verification, support-file tampering, receipt bounds/schema, internal/external/root/receipt symlinks. |
| `native.py` | `test_native_builder.py`, `test_agent.py` | Framework/library selection, all-five concurrent builds, deterministic output order, duplicate names, worker bounds, empty/invalid requests, shared cache locks, source/compiler invalidation, corrupt metadata, missing output, staged publication, failed/interrupted rebuild cleanup. |
| `process.py` | `test_process.py`, `test_validation.py` | Real simultaneous pipe output, UTF-8 replacement, nonzero exits, exact/exceeded byte limits, unavailable command, wall deadline, inherited child pipes, KeyboardInterrupt, thread cancellation before/during launch, process groups, mocked Windows taskkill success/failure/exit races, reaping, owned file-read completion before cancellation cleanup. |
| `isolation.py` | `test_contracts.py` | Every resource budget's type/range, finite deadlines, required guarantee sets, capability support, unavailable build/run implementation. |
| `validation.py` | `test_contracts.py`, `test_validation.py`, `test_validation_failures.py` | All manifest/local-policy fields, typed immutable jobs, C11 entrypoints, UTF-8 byte bounds, comments/literals/includes/dependencies, path/capability rejection, platform/architecture matching, compile failure classes, repair correlation and cap, repeated source/diagnostics, sanitizer privacy, fixture smoke/functional checks, framing, blocked/limited preflight, artifact size/symlink/integrity/cleanup. |
| `executor.py` | `test_executor.py`, `test_validation.py` | Every authorization/validation/capability/boundary gate, private stdin validation, runtime failures and limits, local output opt-in, single-use execution, binary tampering/disappearance/growth, concurrent claims, slow threaded hashes, cancellation/deadline behavior. |
| `jobs.py` | `test_jobs.py`, `test_validation.py` | Every message route, stable/duplicate/stale IDs, replay/retention limits, shared worker capacity, repair waits releasing slots, active/queued/ready cancellation, disconnects, grant revocation, result backpressure/publication failure, sanitized internal errors, new work after failure, blocked/busy/single-use execution. |
| `bridge.py` | `test_bridge.py`, `test_agent.py`, `test_validation.py` | Scalar/URL/command validation, all button/scroll choices, bounded outbox/latest samples, JSON/event/framing rejection, acknowledgement correlation and ordering, per-device concurrency/queue overflow, ping/add/revision handling during slow work, concurrent startup/failure/cancellation, graceful/terminate/kill shutdown, normal WebSocket close, backoff cap/reset, authentication. |
| `__init__.py` | Import/discovery across the suite | Empty package marker; no executable behavior. |

The defensive final return and exhausted-loop exit in `validate_job` cannot be
reached with its validated positive attempt limit and mandatory per-attempt
return/failure branches. They remain visible in the coverage report rather than
being excluded.

## Native C matrix

`test_native_tools.py` generates trusted harnesses that include each complete
standalone source and rename its production `main`. It calls portable helpers
directly and runs invalid CLI cases that return before OS access. These tests
run on supported SDKs and on Linux fallback branches.

| Program | Native unit checks |
| --- | --- |
| All five | Valid/boundary/NaN/infinite/overflow/malformed numeric parsing; JSON quote/control/UTF-8/replacement behavior; timestamps and sequence numbers; event/error framing; invalid/incomplete CLI arguments. |
| `volume.c` | Integer interval limits; nonblocking input with partial writes, CRLF, exact capacity, overlong lines, recovery, EOF; invalid commands and structured inaccessible-device errors on Linux. |
| `mouse.c` | Speed bounds; the same nonblocking input cases; joystick/button/scroll syntax and denied-input replies; joystick/button state JSON on Linux. |
| `metrics.c` | Sampling interval bounds; local sensor file boundaries, missing/empty/invalid/stale readings; partial RAM/temperature availability and source/status JSON on Linux. |
| `processes.c` | CPU/RAM threshold equality, unknown resource values, ancestor context, PID subtree/all/absent selection, binary search, reused parent PID, self-parent/cycles; multicore CPU calculation, zero interval, reused process PID, regressed CPU counter, missing measurements; unsupported enumeration error on Linux. |
| `screen.c` | Capture interval/size bounds; base64 empty/padded/all-byte inputs and multiple flush buffers; structured capture permission/unavailable error on Linux. |

`test_agent.py` also includes independent native regression fixtures and a fresh
SDK compilation of all five tools on macOS/Windows. POSIX executable-fixture
tests and privileged symlink fixtures have explicit platform skips. Linux skips
the SDK compilation test. Successful audio, mouse, screen, hardware-sensor, and
process-enumeration API behavior still needs target-machine integration tests;
these unit fixtures do not establish native permission or sandbox containment.

## Concurrent work and enforced ordering

| Work | Parallelism and bounds | Regression proof |
| --- | --- | --- |
| Repository-owned builds | Up to five compiler threads/processes; local `build_tools(..., workers=1..5)` can reduce it. Same-tool cache publication has a lock; outputs are staged. A failed/interrupted build cancels siblings and waits for cleanup. | A five-party barrier must be reached before any compile completes; concurrent callers publish one same-tool build; failed/interrupted staging leaves no invalid cache. |
| Native startup/telemetry | Four native processes start concurrently; `--screen` adds a fifth. Each has a separate async stdout reader, so sampling continues independently. Startup failure/cancellation waits for every spawn and closes every process. | Startup barriers, failure/cancellation fixtures, real subprocess and WebSocket stream tests. |
| Device controls | One ordered worker each for mouse and volume, with 32 queued commands per device. Different devices progress concurrently; the receiver continues reading job messages. A missing acknowledgement causes reconnection before another command can receive its late reply. | Slow-volume/mouse/ping/revision tests, same-device ack-order test, queue overflow, worker-failure cancellation. |
| Generated build/run operations | Up to four shared worker slots by default, adjustable locally to 1–4. Up to four distinct jobs/artifacts retained per connection. Repair waits hold no worker slot. The actual adapter must be async, support concurrent job directories, and enforce OS budgets. | Four-party build barrier, local two-worker cap, one-worker repair/progress test, cancellation of active/queued work. |
| Artifact hashes | File reads/hashing run in executor threads. The event loop rechecks validity/consumption and claims authorization once before execution. Cancellation waits for the owned reader before deleting its files; disconnect invalidates ready artifacts immediately. | Two simultaneous hash checks launch only one execution; a stalled hash does not block the event loop; cancellation/disconnect waits for thread cleanup. |
| Job revisions and results | A job's initial/revised source, preflight, and execution depend on earlier stages. One WebSocket sender preserves valid frame writes; results are bounded/prioritized and samples coalesce. | Revision-ID/attempt/scope tests, single-use races, slow-consumer and privacy tests. |

Native features already use separate OS processes; adding extra threads inside
their small sampling loops would not create more independent work. The shared
pool limits aggregate load, and OS scheduling determines which workers execute
simultaneously on available cores. Per-device acknowledgements, job revisions,
and artifact claims retain ordering because their later steps depend on prior
results.

Generated-code processing remains blocked until a verified native isolation
adapter is installed. Concurrency, subprocess deadlines, and temporary files do
not provide that boundary. See [the validation protocol](validation-protocol.md).
