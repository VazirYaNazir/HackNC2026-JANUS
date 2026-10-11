"""Local native-tool commands and an authenticated live backend connection."""
import argparse
import asyncio
from contextlib import redirect_stdout
import json
import os
import platform
import subprocess
import sys

from .compiler import CompilerError, detect_compiler
from .native import TOOLS, build_tools


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="JANUS laptop tools (macOS / Windows)")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="Compile all five standalone tools")
    worker = commands.add_parser("worker", help="Prepare or inspect the shipped portable-C execution worker")
    worker.add_argument("action", choices=("status", "prepare", "diagnose"))
    worker.add_argument("--isolation-adapter", default=(os.getenv("AGENT_ISOLATION_ADAPTER") or "").strip() or None, help="Preserve an explicitly configured local adapter")
    run = commands.add_parser("run", help="Build and run one tool; JSON Lines on stdout")
    run.add_argument("tool", choices=TOOLS)
    run.add_argument("args", nargs=argparse.REMAINDER)
    stream = commands.add_parser("stream", help="Stream telemetry and receive backend commands")
    stream.add_argument("--url", default=os.getenv("AGENT_BACKEND_URL"))
    stream.add_argument("--screen", action="store_true", help="Enable primary-screen JPEG streaming")
    stream.add_argument("--screen-interval", type=int, default=1000)
    stream.add_argument("--min-cpu", type=float, default=1)
    stream.add_argument("--min-ram-mb", type=float, default=100)
    stream.add_argument("--temperature-file", help="Optional local sensor-provider file, Celsius")
    stream.add_argument("--validation-policy", help="Local JSON grants by job ID; never supplied by the backend")
    connection = commands.add_parser("connect", help="Connect a registered device for generated jobs (protocol v2)")
    connection.add_argument("--url", default=os.getenv("AGENT_BACKEND_URL"))
    connection.add_argument("--device-id", default=os.getenv("AGENT_DEVICE_ID"))
    connection.add_argument("--validation-policy", help="Local JSON grants by job ID")
    approval = connection.add_mutually_exclusive_group()
    approval.add_argument("--approve-session", action="store_true", help="Ask this laptop's terminal to approve the local session policy for fresh jobs")
    approval.add_argument("--session-approval-stdin", action="store_true", help="Read one local startup approval from the already-consented launcher's stdin pipe")
    connection.add_argument("--workers", type=int, default=4, help="Local concurrent worker limit, 1–4")
    connection.add_argument("--isolation-adapter", default=(os.getenv("AGENT_ISOLATION_ADAPTER") or "").strip() or None, help="Local worker module:factory; defaults to the shipped Docker worker")
    connection.add_argument("--native-tools", action="store_true", help="Enable trusted built-in telemetry, volume and mouse control")
    connection.add_argument("--screen", action="store_true", help="Also share primary-screen frames; requires --native-tools")
    connection.add_argument("--screen-interval", type=int, default=1000)
    connection.add_argument("--min-cpu", type=float, default=1)
    connection.add_argument("--min-ram-mb", type=float, default=100)
    connection.add_argument("--temperature-file", help="Optional local Celsius sensor file")
    return cli


def main(argv=None) -> int:
    cli = parser()
    options = cli.parse_args(argv)
    try:
        if options.command == "worker":
            from .isolation import load_boundary
            # Installer progress belongs on stderr; stdout is a machine-readable report.
            with redirect_stdout(sys.stderr):
                if options.action == "prepare" and not options.isolation_adapter:
                    from .container import prepare_worker
                    report = asyncio.run(prepare_worker())
                else:
                    boundary = load_boundary(options.isolation_adapter)
                    describe = getattr(boundary, "describe", None)
                    report = describe() if callable(describe) else {
                        "available": boundary.can_build() and boundary.can_run(),
                        "reason": None if boundary.can_build() and boundary.can_run() else "WORKER_UNAVAILABLE",
                        "execution_target": None,
                    }
                    diagnose = getattr(boundary, "diagnose", None)
                    if options.action == "diagnose" and callable(diagnose):
                        report = diagnose()
            print(json.dumps(report, allow_nan=False))
            return 0 if options.action in {"status", "diagnose"} or report.get("available") else 1
        if options.command == "build":
            for name, path in build_tools().items():
                print(f"{name}: {path}")
            return 0
        if options.command == "run":
            binary = build_tools((options.tool,))[options.tool]
            args = options.args[1:] if options.args[:1] == ["--"] else options.args
            return subprocess.call([str(binary), *args])
        if options.command == "connect":
            from .transport import run_connection, validate_options as validate_connection_options
            from .jobs import ValidationService
            from .validation import load_policies
            from .isolation import load_boundary
            validate_connection_options(options)
            from .bridge import scalar
            scalar(options.screen_interval, 200, 60000)
            scalar(options.min_cpu, 0, 100000)
            scalar(options.min_ram_mb, 0, 1000000)
            if options.screen and not options.native_tools:
                raise ValueError("--screen requires --native-tools")
            token = os.getenv("AGENT_TOKEN")
            if not token:
                cli.error("Set AGENT_TOKEN in your local environment")
            policies = load_policies(options.validation_policy)
            session_authorization = None
            if options.approve_session or options.session_approval_stdin:
                from .authorization import approve_session, load_session_scope
                session_authorization = approve_session(load_session_scope(options.validation_policy), launcher_pipe=options.session_approval_stdin)
            boundary = load_boundary(options.isolation_adapter)
            worker_compiler = getattr(boundary, "compiler", None)
            target = getattr(boundary, "execution_target", None)
            if isinstance(target, dict) and boundary.can_build() and boundary.can_run():
                local_platform = (target["platform"], target["architecture"])
            else:
                local_platform = None
            host_compiler = detect_compiler() if options.native_tools or worker_compiler is None else None
            compiler = worker_compiler or host_compiler
            validation = ValidationService(compiler, policies, boundary=boundary, max_parallel=options.workers,
                                           session_authorization=session_authorization, local_platform=local_platform,
                                           host_platform=(platform.system(), platform.machine()))
            if options.native_tools:
                names = TOOLS if options.screen else tuple(name for name in TOOLS if name != "screen")
                binaries = build_tools(names, compiler=host_compiler)
                asyncio.run(run_connection(options, token, validation, binaries))
            else:
                asyncio.run(run_connection(options, token, validation))
            return 0
        from .bridge import run_bridge, validate_options
        from .jobs import ValidationService
        from .validation import load_policies
        validate_options(options)
        token = os.getenv("AGENT_TOKEN")
        if not token:
            cli.error("Set AGENT_TOKEN in your local environment (never pass it on the command line)")
        policies = load_policies(options.validation_policy)
        compiler = detect_compiler()
        binaries = build_tools(compiler=compiler)
        validation = ValidationService(compiler, policies)
        asyncio.run(run_bridge(options, binaries, token, validation))
        return 0
    except KeyboardInterrupt:
        return 130
    except (CompilerError, OSError, ValueError, RuntimeError) as error:
        if options.command == "worker":
            print(json.dumps({"available": False, "reason": "WORKER_PREPARATION_FAILED", "execution_target": None}))
            print("Local worker check failed. Check Docker Desktop or the configured local adapter and retry.", file=sys.stderr)
            return 1
        print(f"Agent error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
