# Test the complete application

From the repository root, install Node 22+, `uv`, a C compiler, and the locked dependencies:

```sh
npm ci
uv sync --locked --directory agent
uv sync --locked --directory backend
npx playwright install chromium
```

On a Linux CI machine, use `npx playwright install --with-deps chromium` to install browser system libraries too. macOS needs Xcode Command Line Tools; Windows native builds need a Visual Studio Developer Command Prompt with the Windows SDK.

```sh
npm test                  # all unit suites, coverage gates, lint, both builds, browser workflows
npm run test:unit         # all Python and TypeScript unit suites
npm run test:coverage     # full source coverage and HTML reports
npm run test:e2e          # lint/build both apps, then run browser workflows
```

Coverage reports are written to `coverage/index.html`, `agent/htmlcov/index.html`, and `backend/htmlcov/index.html`. Python coverage measures statements and branches, with existing gates of 99% for the complete agent package and 95% for the complete backend app. JavaScript reports include both apps and all shared packages, including unvisited files, with gates of 80% statements, 70% branches, 75% functions and 85% lines; they do not claim 100% coverage. Generated reports and browser traces are ignored by Git.

Unit tests cover all widget families, ranges and thresholds, input/edit modes, formatting, charts and logs, process hierarchy and cycle handling, screen errors, project changes and undo/redo, import/export validation, connection and pairing forms, saved phone credentials, install prompts, the demo client, live subscriptions and stale data, reuse approval, native queues and StrictMode cleanup. The Python suites cover standalone C harnesses, compiler/cache behavior, validation and repair, worker concurrency and cancellation, protocol framing, native process startup/shutdown, per-device controls, Gemini/provider behavior, SQLite operations, authenticated routes, fanout, legacy/admin APIs, and tunnel lifecycle.

Browser tests use production builds, a real HTTP/WebSocket backend, real temporary SQLite databases, and the actual QR decoder with a test camera. The generated-program fixture compiles and executes only exact author-written C examples; it is not a production isolation adapter. Native telemetry and commands use a deterministic fixture rather than controlling a real desktop. A browser-generated JPEG passes through the agent protocol, backend, and both real screen widgets.

GitHub Actions runs the complete integration suite on Linux and builds all five standalone C tools against actual macOS and Windows SDKs. OS build checks do not prove that microphone/audio devices, mouse permissions, camera permissions, screen-recording permissions, or CPU temperature hardware are available. Verify those on the intended laptop and phone:

1. Start the registered agent with `--native-tools`, adding `--screen` only when screen sharing is intended.
2. Create **Project → Laptop controls starter** in Studio and **Save & Push**.
3. Pair the phone with the actual HTTPS backend through QR scanning or manual code entry.
4. Adjust volume, hold and release the joystick, switch away from the phone app, and verify that mouse movement stops.
5. Check RAM, available CPU temperature, relevant process ancestors, and the actual screen. Apple Silicon temperature may need the documented local sensor file.
6. Stop/restart the agent; readings should disappear when offline and resume when it reconnects.
