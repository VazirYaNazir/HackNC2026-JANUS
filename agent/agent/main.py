"""Local native-tool commands and an authenticated live backend connection."""
import argparse
import asyncio
import os
import subprocess
import sys

from .compiler import CompilerError, detect_compiler
from .native import TOOLS, build_tools


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Janus laptop tools (macOS / Windows)")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="Compile all five standalone tools")
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
    return cli


def main(argv=None) -> int:
    cli = parser()
    options = cli.parse_args(argv)
    try:
        if options.command == "build":
            for name, path in build_tools().items():
                print(f"{name}: {path}")
            return 0
        if options.command == "run":
            binary = build_tools((options.tool,))[options.tool]
            args = options.args[1:] if options.args[:1] == ["--"] else options.args
            return subprocess.call([str(binary), *args])
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
    except (CompilerError, OSError, ValueError) as error:
        print(f"Agent error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
