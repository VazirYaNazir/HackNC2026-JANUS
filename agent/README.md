# JANUS laptop agent

Five **standalone C11 programs** implement native laptop features on macOS and
Windows. Each `.c` file can be compiled independently, uses OS libraries, accepts
options/commands, and writes flushed JSON Lines to stdout. A Python bridge runs
them as persistent subprocesses and forwards samples to an authenticated backend
WebSocket. Native tools and local execution code live in this `agent/` directory.

## Run

Use macOS 12+ or Windows 10+ in a logged-in desktop session, Python 3.12+, and
`uv`. On macOS, install the Xcode Command Line Tools (`xcode-select --install`);
the native tools require Apple's SDK/frameworks. On Windows, use a Visual Studio
Developer Command Prompt with the C compiler and Windows SDK, or MinGW-w64 GCC.
Compiler detection also supports an existing Zig compiler installed by the
repository's fallback installer. Linux is not a supported laptop target.

From the repository's `agent/` directory:

```sh
uv sync --locked
uv run --no-sync python -m agent.main build
uv run --no-sync python -m agent.main run metrics --once
uv run --no-sync python -m agent.main run processes --once --min-cpu 1 --min-ram-mb 100
uv run --no-sync python -m agent.main run volume
```

The last command stays running: type `set 35` followed by Enter to set volume,
or `stop` to exit. Binaries are cached under ignored `.native-bin/`, rebuilt when
their source or compiler changes, and may be run directly without Python.
The builder compiles independent tools in up to five threads. Each build stages
its output before replacing the cache; interruption or failure cancels active
compiler commands and removes unfinished staging directories.

For Studio and the PWA, use the registered v2 connection instead of the legacy
streaming path:

```sh
uv run --locked python -m agent.main connect --url ws://127.0.0.1:8000/ws/agents/my-laptop --device-id my-laptop --native-tools --screen
```

Set `AGENT_TOKEN` to this registered laptop's agent credential. `--native-tools`
enables trusted built-in controls and telemetry; `--screen` independently opts
into screen sharing. The four non-screen tools run without `--screen`. Use
`--min-cpu`, `--min-ram-mb`, `--screen-interval` and `--temperature-file` to tune
native sampling. Repository-owned tools do not require a generated-job grant;
generated C still uses the existing local grants and isolation boundaries.

For legacy streaming, configure `AGENT_TOKEN` securely in the local environment to match
the backend's agent token; configure `AGENT_BACKEND_URL` to its `/ws/agent` URL.
No credentials are printed or stored by the agent. Remote connections require
`wss://`; `ws://` is accepted only for localhost/loopback development.

```sh
uv run --no-sync python -m agent.main stream --url wss://your-backend/ws/agent
# Explicitly include screen frames:
uv run --no-sync python -m agent.main stream --url wss://your-backend/ws/agent --screen
```

The bridge reconnects with a bounded delay, restarts its native tools per
connection, stops the tools on disconnect, and retains only the most recent
sample per stream when the network is slow. Command acknowledgements are
prioritized. The startup `agent_hello` message lists enabled streams and commands.
Use Ctrl+C to stop. Screen capture starts only with `--screen`, and cannot be
enabled remotely through the command protocol.
On macOS this local opt-in requests Screen Recording access once. Enable your
terminal or JANUS in **System Settings → Privacy & Security → Screen Recording**
and restart JANUS when macOS requires it. Windows capture requires the signed-in
user's unlocked interactive desktop. JPEG frames are automatically reduced to
fit the backend's live-frame limit; a capture failure appears as a stream error.

## Generated C validation and repair

For the device-specific backend/Gemini pipeline, use `python -m agent.main connect`
with `--device-id`, the matching `/ws/agents/<device-id>` URL, local
`--validation-policy`, and optional `--workers 1..4`. Set that laptop's
`AGENT_TOKEN` in its environment. This protocol v2 connection carries job events
without starting native telemetry or screen capture. It uses heartbeats and
reconnects with bounded exponential backoff and jitter.

An existing audited local worker can be selected with
`--isolation-adapter my_local_app.worker:create_boundary` or
`AGENT_ISOLATION_ADAPTER`. This is a local factory implementing the existing
`IsolationBoundary`; no cloud command can select it. Interface checks establish
conformance rather than verifying OS containment. The default adapter uses the
shipped Docker worker and requires verified local Linux isolation. See the [backend–agent setup, protocol, endpoints, and
tests](../backend/app/docs/backend-agent.md).

If generated programs report an unavailable worker, collect its local readiness
checks from the repository root:

```sh
uv run --locked --directory agent python -m agent.main worker diagnose
```

The JSON report includes safe diagnostic stages and failed checks collected by
the worker probe. It excludes credentials, environment contents and raw Docker
errors. `worker status` retains its compact availability report; `worker prepare`
attempts to prepare the shipped image. Custom adapters without a diagnostic
method fall back to their availability report.

The worker verifies its own network namespace through `/proc/self/net/dev` and
requires loopback alone. Docker still uses `--network none`, and generated
programs cannot create sockets. This avoids relying on a sysfs network view
that can differ from the process namespace on Docker Desktop.

After changes to `agent/worker/runner.py` or its Dockerfile, run `worker prepare`
to build the matching image, then fully restart JANUS. `npm start` also prepares
the current worker image before starting the agent.

The bridge also routes `validate_job`, `code_revision`, `execute_job`, and
`cancel_job` messages. Validation enforces a local per-job policy, a 128 KiB C11
source contract, at most three compilations, sanitized backend repair requests,
and a separate artifact/execution authorization gate. Runtime outputs stay local
unless the local policy explicitly opts into sharing them.

The full application launcher prepares `agent/.validation-policy.json` with a
session scope covering every supported capability and the current OS libraries,
then asks for local startup permission. This file describes scope and cannot
activate session approval by itself. Approval lasts until the agent process stops;
fresh request IDs receive individual local grants, and consumed IDs cannot be
replayed after reconnecting. The launcher sends the approved agent one local
stdin-pipe message so no second prompt is needed. Approval is never saved to disk.

For a direct agent launch, copy [the complete policy example](validation-policy.example.json)
to `.validation-policy.json`, set the registered laptop's `AGENT_TOKEN`, then use:

```sh
uv run --locked python -m agent.main connect \
  --url ws://127.0.0.1:8000/ws/agents/laptop-1 --device-id laptop-1 \
  --validation-policy .validation-policy.json --approve-session \
  --native-tools --screen --workers 4
```

Review the disclosed scope and type `APPROVE` in your local terminal. A declined
or noninteractive prompt stops startup. Remove capability/dependency names or
replace the `operations: ["*"]` wildcard with specific operation names to narrow
the scope. Existing explicit `jobs` grants take precedence over session defaults.
The example includes both OS dependency sets; each generated job must still target
the current OS and declare only its available libraries. Runtime budgets may be
reduced in `session.limits`; startup approval cannot expand their supported maxima.

Generated-code compilation and execution **are blocked by default**: a verified
macOS/Windows isolation worker is not supplied. The five repository-owned tools
continue to work. A local `--validation-policy .validation-policy.json` file can
register grants but cannot enable isolation. See the
[protocol and local-worker interface](docs/validation-protocol.md) for message
schemas, local policy, resource limits, and activation requirements. Backend
handlers and LLM calls are outside this agent-only change.

## Tools

| Program | Input/options | Live output |
| --- | --- | --- |
| `volume.c` | stdin `set 0..100`, `stop`; `--once`, `--interval 500` | Default output volume percentage and mute state. Uses Core Audio or Windows endpoint volume. Output devices with fixed hardware volume can return an error. |
| `mouse.c` | stdin `move x y`, `button left\|right\|middle down\|up`, `scroll integer`, `stop`; `--speed 900` | Joystick state, held buttons, and cursor position every 100 ms. Uses CGEvent or SendInput. |
| `metrics.c` | `--once`, `--interval 1000`, `--temperature-file path` | Total/used physical RAM and percentage, CPU temperature in Celsius when readable, sensor source/status. |
| `processes.c` | `--once`, `--interval 1000`, `--min-cpu 1`, `--min-ram-mb 100`, `--pid PID`, `--all` | Flat process tree with PID/parent/name, CPU percentage, RAM bytes and ancestor context flags. |
| `screen.c` | `--once`, `--interval 1000`, `--max-width 960` | Primary display JPEG frames, base64 payload, MIME type, scaled dimensions. |

**Joystick semantics:** `x`/`y` are normalized axes in `[-1,1]`; right/up are
positive. Magnitudes below 0.08 are ignored. They control velocity, not absolute
coordinates. Send fresh values at 20–60 Hz, including zero when centered. At
250 ms without a fresh move/button event, motion stops and held buttons release.
EOF and normal shutdown also release held buttons. macOS motion is clamped to
the primary display; Windows uses the system's relative pointer acceleration.
Windows input cannot control higher-integrity/elevated apps or the secure desktop.

**Process relevance:** by default, select processes using at least 1% of one
CPU core **or** 100 MiB resident RAM. Include their ancestors as `context: true`
to retain tree structure, without adding unrelated descendants. Defaults can be
adjusted both locally and with bridge `--min-cpu`/`--min-ram-mb`. `--pid` selects
that process and its descendants plus ancestor context; `--all` bypasses the
resource filter. Without `--all`, scope is the current user on macOS and current
interactive session on Windows. CPU usage comes from two samples of cumulative
process CPU time; 100% means one core, and multicore processes may exceed 100%.
A new or unreadable process reports `cpu_percent: null`, never a fabricated zero.
The first report waits one sampling interval. PID reuse is checked against birth
time. `parent_pid: null` marks roots in the filtered view; `ppid` retains the OS
parent PID. Windows RAM is working set; macOS RAM is resident size.

**Temperature availability:** there is no universal CPU temperature API on
either OS. On Windows, metrics queries CPU temperature sensors exposed by a
running [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
or OpenHardwareMonitor WMI provider, reporting the hottest CPU sensor (not an
ACPI thermal-zone estimate). On Intel Macs, it tries AppleSMC CPU keys; access
and key availability depend on the machine. Apple Silicon does not have a
supported public CPU-temperature API here. On any machine, an existing trusted
local sensor provider can write a file containing one numeric Celsius reading;
pass `--temperature-file /path/to/reading` to the C tool or bridge. Update the
file atomically to avoid partial readings; readings older than five seconds are
reported as unavailable. Otherwise temperature is `null` with
an explicit status, while RAM and the other streams continue. The agent never
requests elevation or silently installs a kernel driver. macOS RAM used is
active + wired + compressed pages, excluding reclaimable inactive cache;
Windows RAM used is total minus available physical memory.

**Desktop permissions:** grant Accessibility permission to the terminal/native
mouse executable in macOS System Settings to use mouse control. Grant Screen
Recording permission to the capture process/terminal for screenshots, then
restart it if requested by macOS. macOS capture uses the system's
`/usr/sbin/screencapture` utility, avoiding the deprecated CGDisplayCreateImage
API, followed by native ImageIO JPEG encoding. Windows uses GDI and WIC. Both
capture the primary display only. Frames preserve aspect ratio with the longest
edge bounded by `--max-width`; defaults are 960 pixels and about one frame per
second. This is a live dashboard snapshot stream, not a high-FPS remote desktop.
Permission failures emit structured errors rather than blank success frames.

## Backend integration contract

The existing backend `/ws/agent` recognizes **only `job_result`** today. The
bridge supports its `ping` and `add` jobs already. It also sends the messages
below; the backend needs handlers to relay telemetry to its frontend subscribers
and to forward controls to the agent. Those backend/frontend changes are outside
this agent-only implementation, so there is no end-to-end frontend relay yet.

Native tools and the bridge use this event envelope:

```json
{"type":"telemetry","stream":"volume","timestamp_ms":1790000000000,"seq":12,"ok":true,"data":{"value":35.0,"muted":false}}
```

Streams are `volume`, `mouse`, `metrics`, `processes`, and optionally `screen`.
Timestamps are Unix milliseconds; sequence numbers are local to each subprocess
and restart on reconnection. Errors use `type: "error"`, `ok: false`, and an
`error` message. Metrics temperature unavailability is represented inside its
normal data event so valid RAM samples can continue.

The backend sends controls as JSON, with a nonempty `request_id`:

```json
{"type":"command","request_id":"volume-42","target":"volume","action":"set","args":{"value":42}}
{"type":"command","request_id":"stick-1","target":"mouse","action":"move","args":{"x":0.5,"y":-0.25}}
{"type":"command","request_id":"press-1","target":"mouse","action":"button","args":{"button":"left","state":"down"}}
{"type":"command","request_id":"scroll-1","target":"mouse","action":"scroll","args":{"amount":-1}}
```

The bridge replies after the native API operation:

```json
{"type":"command_result","stream":"volume","request_id":"volume-42","timestamp_ms":1790000000000,"seq":13,"ok":true,"data":{"value":42.0,"muted":false}}
```

Invalid controls return `ok: false` and an `error`. Commands for one device stay
ordered, while mouse and volume use separate workers so a slow acknowledgement
cannot block the other device or job messages. Each device queue holds at most
32 pending commands; excess commands return an error. Native control commands
accept only the actions above. Generated C uses the separate validation protocol
and requires a local grant and verified isolation before it can be compiled.
For joystick movement, the acknowledgement means the velocity was accepted by
the input API; subsequent mouse telemetry supplies cursor position. Sensor and
desktop permission failures remain observable as events.

A screen event's `data` contains:

```json
{"mime":"image/jpeg","width":960,"height":540,"display":"primary","base64":"..."}
```

The frontend can render the frame using `data:image/jpeg;base64,` plus `base64`.
The backend should allow outbound frame messages up to 6 MiB (JPEG bytes are
capped at 4 MiB), keep only the latest frame, and authenticate frontend
subscribers/control requests before forwarding them.

## Compile a C file directly

macOS:

```sh
cc -std=c11 -O2 native/volume.c -o volume -framework CoreAudio -framework CoreFoundation
cc -std=c11 -O2 native/mouse.c -o mouse -framework ApplicationServices
cc -std=c11 -O2 native/metrics.c -o metrics -framework IOKit -framework CoreFoundation
cc -std=c11 -O2 native/processes.c -o processes
cc -std=c11 -O2 native/screen.c -o screen -framework CoreGraphics -framework ImageIO -framework CoreFoundation
```

Windows (MinGW-w64):

```sh
gcc -std=c11 -O2 native/volume.c -o volume.exe -lole32 -luuid
gcc -std=c11 -O2 native/mouse.c -o mouse.exe -luser32
gcc -std=c11 -O2 native/metrics.c -o metrics.exe -lole32 -loleaut32 -lwbemuuid
gcc -std=c11 -O2 native/processes.c -o processes.exe -lpsapi
gcc -std=c11 -O2 native/screen.c -o screen.exe -lole32 -loleaut32 -lwindowscodecs -lgdi32 -luser32 -luuid
```

The Python builder supplies the equivalent `.lib` arguments and `/std:c11` when
using MSVC. No shared local headers or generated C dependencies are required.

## Validation

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
# Check combined Python statement/branch coverage (99% minimum):
uv run --locked coverage run -m unittest discover -s tests
uv run --locked coverage report -m
```

The suite covers every Python module, portable C logic in all five tools, actual
subprocess/WebSocket transport, and concurrent execution/cancellation races.
See the [detailed test matrix and concurrency model](docs/testing.md). Python
coverage measures both statements and branches; native C coverage is separate.
Generated-code worker replies are simulated and establish no sandbox containment.
On macOS/Windows, C harnesses and a separate fresh build use the installed native
SDK. The SDK build test is skipped on Linux; successful desktop/hardware API calls
still require target-machine verification. Windows x86-64 builds
were cross-compiled from Linux with Zig 0.15.2. macOS SDK compilation, MSVC
compilation, hardware sensors, mouse input, audio devices, and desktop capture
must still be verified on target machines. On each target, build all tools;
run each telemetry tool with `--once`; change volume and verify the real device;
send joystick/drag/scroll events and verify expiry; check process filtering with
a known busy application; capture and decode one JPEG; then test streaming
against a backend that implements the contract above.
