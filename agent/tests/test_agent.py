import asyncio
import base64
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from agent.bridge import Outbox, command_line, connected_session, validate_options
from agent.compiler import CCompiler, detect_compiler
from agent.native import ROOT, TOOLS, link_arguments


class NativeBuildTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform in ("darwin", "win32"), "Requires a macOS or Windows SDK")
    def test_all_tools_build_against_native_sdk(self):
        # Compile fresh outputs to catch SDK header/link failures, even if the
        # normal builder already has cached executables.
        compiler = detect_compiler()
        with tempfile.TemporaryDirectory() as tmp:
            for name in TOOLS:
                with self.subTest(tool=name):
                    binary = Path(tmp) / (name + (".exe" if sys.platform == "win32" else ""))
                    compiler.compile(ROOT / "native" / f"{name}.c", binary, timeout=120, link_args=link_arguments(name, compiler))
                    self.assertTrue(binary.is_file())


class CommandTests(unittest.TestCase):
    def test_reject_nonfinite_out_of_range_and_shell_inputs(self):
        for value in (None, True, math.nan, math.inf, -1, 101, "50\nstop", 10**1000):
            with self.subTest(value=repr(value)[:40]), self.assertRaises(ValueError):
                command_line({"target": "volume", "action": "set", "args": {"value": value}})
        with self.assertRaises(ValueError):
            command_line({"target": "mouse", "action": "move", "args": {"x": 1.01, "y": 0}})
        with self.assertRaises(ValueError):
            command_line({"target": "mouse", "action": "scroll", "args": {"amount": 1.5}})
        with self.assertRaises(ValueError):
            command_line({"target": "screen", "action": "exec", "args": {"code": "bad"}})

    def test_protocol_translations(self):
        self.assertEqual(command_line({"target": "volume", "action": "set", "args": {"value": 37}}), ("volume", "set 37.000000\n"))
        self.assertEqual(command_line({"target": "mouse", "action": "move", "args": {"x": -0.5, "y": 0.25}}), ("mouse", "move -0.500000 0.250000\n"))
        self.assertEqual(command_line({"target": "mouse", "action": "button", "args": {"button": "left", "state": "down"}}), ("mouse", "button left down\n"))

    def test_lock_translates_to_the_mouse_tool_and_takes_no_arguments(self):
        self.assertEqual(command_line({"target": "mouse", "action": "lock", "args": {}}), ("mouse", "lock\n"))
        with self.assertRaises(ValueError):
            command_line({"target": "mouse", "action": "lock", "args": {"force": True}})
        with self.assertRaises(ValueError):
            command_line({"target": "volume", "action": "lock", "args": {}})

    def test_remote_requires_tls(self):
        options = SimpleNamespace(url="ws://example.com/ws/agent", screen_interval=1000, min_cpu=1, min_ram_mb=100)
        with self.assertRaises(ValueError):
            validate_options(options)
        for url in ("wss://example.com/ws/agent", "ws://127.0.0.1:8000/ws/agent"):
            options.url = url
            validate_options(options)
        options.url = "wss://user:password@example.com/ws/agent"
        with self.assertRaises(ValueError):
            validate_options(options)

    def test_platform_link_arguments(self):
        for name in TOOLS:
            self.assertTrue(all(x.endswith(".lib") for x in link_arguments(name, CCompiler("msvc", "cl", "Windows"))))
        self.assertIn("CoreAudio", link_arguments("volume", CCompiler("clang", "clang", "Darwin")))


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_backpressure_preserves_latest_samples_and_results(self):
        box = Outbox()
        for i in range(1000):
            box.sample({"type": "telemetry", "stream": "screen", "seq": i})
        await box.result({"type": "command_result", "request_id": "v1"})
        self.assertEqual(len(box.samples), 1)
        self.assertEqual((await box.get())["request_id"], "v1")
        self.assertEqual((await box.get())["seq"], 999)

    @unittest.skipIf(os.name == "nt", "Test fixture uses POSIX executable scripts")
    async def test_live_websocket_commands_telemetry_and_cleanup(self):
        # Native OS APIs cannot run in Linux. Real subprocess pipes and a real
        # WebSocket server exercise the transport, acknowledgements and cleanup.
        with tempfile.TemporaryDirectory() as tmp:
            binaries = {}
            for name in TOOLS:
                script = Path(tmp) / name
                script.write_text(f'#!{sys.executable}\n' + '''import json, os, sys, threading, time
name=os.path.basename(sys.argv[0])
lock=threading.Lock()
def emit(value):
    with lock:
        print(json.dumps(value),flush=True)
def sample():
    while True:
        emit({"type":"telemetry","stream":name,"data":{"pid":os.getpid()}})
        time.sleep(0.02)
threading.Thread(target=sample,daemon=True).start()
for line in sys.stdin:
    if line.strip()=="stop": break
    emit({"type":"command_result","stream":name,"ok":True,"data":{"line":line.strip()}})
''')
                script.chmod(0o700)
                binaries[name] = script
            options = SimpleNamespace(screen=True, screen_interval=1000, min_cpu=1, min_ram_mb=100, temperature_file=None)
            pids = set()
            finished = asyncio.get_running_loop().create_future()

            async def handler(ws):
                try:
                    self.assertEqual(ws.request.headers["Authorization"], "Bearer fixture-token")
                    async def until(predicate):
                        for _ in range(200):
                            event = json.loads(await asyncio.wait_for(ws.recv(), 3))
                            if event.get("type") == "telemetry":
                                pids.add(event["data"]["pid"])
                            if predicate(event):
                                return event
                        self.fail("Expected event was not received")
                    hello = await until(lambda x: x["type"] == "agent_hello")
                    self.assertEqual(set(hello["streams"]), set(TOOLS))
                    streams = set()
                    while len(streams) != len(TOOLS):
                        event = await until(lambda x: x["type"] == "telemetry")
                        streams.add(event["stream"])
                    await ws.send(json.dumps({"type": "command", "request_id": "slider", "target": "volume", "action": "set", "args": {"value": 65}}))
                    result = await until(lambda x: x.get("request_id") == "slider")
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["data"]["line"], "set 65.000000")
                    await ws.send(json.dumps({"type": "command", "request_id": "stick", "target": "mouse", "action": "move", "args": {"x": 0.5, "y": -0.5}}))
                    result = await until(lambda x: x.get("request_id") == "stick")
                    self.assertEqual(result["data"]["line"], "move 0.500000 -0.500000")
                    await ws.send(json.dumps({"type": "command", "request_id": "bad", "target": "volume", "action": "set", "args": {"value": 101}}))
                    result = await until(lambda x: x.get("request_id") == "bad")
                    self.assertFalse(result["ok"])
                    await ws.send(json.dumps({"type": "job", "job_id": "addition", "action": "add", "args": {"a": 2, "b": 3}}))
                    result = await until(lambda x: x.get("job_id") == "addition")
                    self.assertEqual(result["result"], {"sum": 5})
                    await ws.send(json.dumps({"type": "job", "job_id": "ping", "action": "ping"}))
                    result = await until(lambda x: x.get("job_id") == "ping")
                    self.assertEqual(result["result"], {"pong": True})
                    finished.set_result(None)
                except BaseException as error:
                    finished.set_exception(error)
                finally:
                    await ws.close()

            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}/ws/agent", additional_headers={"Authorization": "Bearer fixture-token"}) as ws:
                    await asyncio.wait_for(connected_session(ws, options, binaries), 15)
                await finished
            self.assertEqual(len(pids), 5)
            for pid in pids:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)


@unittest.skipUnless(shutil.which("cc") and sys.platform != "win32", "Requires a host C compiler")
class NativeLogicTests(unittest.TestCase):
    def compile_run(self, code, *args, input_text=None):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "test.c"
            source.write_text(code)
            binary = Path(tmp) / "test"
            subprocess.run([shutil.which("cc"), "-std=c11", str(source), "-lm", "-o", str(binary)], check=True, capture_output=True)
            return subprocess.run([str(binary), *args], input=input_text, check=True, text=True, capture_output=True).stdout

    @unittest.skipUnless(sys.platform.startswith("linux"), "Tests platform-independent C logic using Linux fallback branches")
    def test_joystick_screen_directions_and_fractional_movement(self):
        path = ROOT / "native" / "mouse.c"
        code = f'#define main tool_main\n#include "{path}"\n#undef main\n' + '''
#include <assert.h>
int main(void) {
    const double directions[][2]={{0,1},{0,-1},{1,0},{-1,0},{0.5,0.5},{-0.5,-0.5},{0,0}};
    const int screen[][2]={{0,-8},{0,8},{8,0},{-8,0},{4,-4},{-4,4},{0,0}};
    for(size_t i=0;i<sizeof(directions)/sizeof(directions[0]);i++) {
        double rx=0,ry=0;int dx,dy;
        joystick_step(directions[i][0],directions[i][1],8,&rx,&ry,&dx,&dy);
        assert(dx==screen[i][0] && dy==screen[i][1]);
        assert(rx==0 && ry==0);
    }
    for(int direction=-1;direction<=1;direction+=2) {
        double rx=0,ry=0;int dx,dy,total_x=0,total_y=0;
        for(int tick=0;tick<4;tick++) {
            joystick_step(direction,direction,0.25,&rx,&ry,&dx,&dy);
            total_x+=dx;total_y+=dy;
            assert(dx==(tick==3?direction:0));
            assert(dy==(tick==3?-direction:0));
        }
        assert(total_x==direction && total_y==-direction);
        assert(rx==0 && ry==0);
        joystick_step(0,0,8,&rx,&ry,&dx,&dy);
        assert(dx==0 && dy==0);
    }
    return 0;
}
'''
        self.compile_run(code)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Tests platform-independent C logic using Linux fallback branches")
    def test_resource_filter_and_ancestors(self):
        path = ROOT / "native" / "processes.c"
        code = f'#define main tool_main\n#include "{path}"\n#undef main\n' + '''
#include <assert.h>
int main(void) {
    Process p={0};p.pid=1;append(&p);
    p.pid=10;p.ppid=1;p.rss=200*1048576ULL;p.ram_known=1;append(&p);
    p.pid=11;p.ppid=10;p.rss=1*1048576ULL;append(&p);
    p.pid=20;p.ppid=1;p.cpu_known=1;p.cpu_percent=3;append(&p);
    qsort(list,length,sizeof(*list),compare_pid);
    select_tree(0,0,1,100);
    assert(list[0].include && !list[0].selected);
    assert(list[1].selected && !list[2].include && list[3].selected);
    for(size_t i=0;i<length;i++) list[i].include=list[i].selected=0;
    select_tree(0,10,1,100);
    assert(list[0].include && list[1].selected && list[2].selected && !list[3].include);
    list[1].birth=20;list[2].birth=10;assert(parent_of(&list[2])==NULL);
    return 0;
}
'''
        self.compile_run(code)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Tests platform-independent C logic using Linux fallback branches")
    def test_json_escaping_and_numeric_validation(self):
        path = ROOT / "native" / "volume.c"
        code = f'#define main tool_main\n#include "{path}"\n#undef main\n' + '''
#include <assert.h>
int main(void) {
    double n;assert(!number("nan",0,100,&n));assert(!number("inf",0,100,&n));
    assert(!number("100\\nstop",0,100,&n));assert(number("50",0,100,&n));
    json_string("quote\\\" newline\\n");return 0;
}
'''
        self.assertEqual(json.loads(self.compile_run(code)), 'quote" newline\n')

    @unittest.skipUnless(sys.platform.startswith("linux"), "Tests platform-independent C logic using Linux fallback branches")
    def test_pipe_recovers_after_overlong_command(self):
        path = ROOT / "native" / "volume.c"
        code = f'#define main tool_main\n#include "{path}"\n#undef main\n' + '''
#include <assert.h>
int main(void) {
    char line[256];int r;
    do {r=input_line(line,sizeof(line));} while(!r);
    assert(r==-2);
    do {r=input_line(line,sizeof(line));} while(!r);
    assert(r==1 && !strcmp(line,"set 35"));return 0;
}
'''
        self.compile_run(code, input_text="x" * 1000 + "\nset 35\n")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Tests platform-independent C logic using Linux fallback branches")
    def test_unicode_and_truncated_process_names_produce_valid_json(self):
        path = ROOT / "native" / "processes.c"
        code = f'#define main tool_main\n#include "{path}"\n#undef main\n' + '''
int main(void) {
    putchar('[');json_string("\\xc3\\xa9\\xf0\\x9f\\x98\\x80");putchar(',');
    json_string("truncated \\xe2\\x82");putchar(']');return 0;
}
'''
        decoded = json.loads(self.compile_run(code))
        self.assertEqual(decoded[0], "é😀")
        self.assertTrue(decoded[1].startswith("truncated "))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Tests platform-independent C logic using Linux fallback branches")
    def test_sensor_file_validation_and_staleness(self):
        path = ROOT / "native" / "metrics.c"
        code = f'#define main tool_main\n#include "{path}"\n#undef main\n' + '''
#include <assert.h>
int main(int argc,char **argv) {
    (void)argc;double value;assert(temperature_file(argv[1],&value));assert(value==42.5);
    assert(!temperature_file(argv[2],&value));assert(!temperature_file(argv[3],&value));return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            good, bad, stale = (Path(tmp) / name for name in ("good", "bad", "stale"))
            good.write_text("42.5\n")
            bad.write_text("nan\n")
            stale.write_text("50\n")
            os.utime(stale, (1, 1))
            self.compile_run(code, str(good), str(bad), str(stale))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Tests platform-independent C logic using Linux fallback branches")
    def test_frame_base64_across_buffer_boundaries(self):
        path = ROOT / "native" / "screen.c"
        code = f'#define main tool_main\n#include "{path}"\n#undef main\n' + '''
int main(void) {
    unsigned char bytes[8195];
    for(size_t i=0;i<sizeof(bytes);i++) bytes[i]=(unsigned char)i;
    base64(bytes,sizeof(bytes));return 0;
}
'''
        raw = bytes(i % 256 for i in range(8195))
        self.assertEqual(self.compile_run(code), base64.b64encode(raw).decode())


if __name__ == "__main__":
    unittest.main()
