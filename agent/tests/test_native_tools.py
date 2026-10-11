"""Author-written C harnesses test all five tools without desktop/hardware access.

Linux fallback branches make native permission failures deterministic. Target SDK
builds remain in test_agent.py; these tests do not emulate successful OS APIs.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from agent.native import ROOT, TOOLS
from agent.native import link_arguments
from agent.compiler import CCompiler, detect_compiler


@unittest.skipUnless(shutil.which("cc") or sys.platform == "win32", "Native C harnesses require a host compiler/SDK")
class NativeToolUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.binaries = {}
        compiler = detect_compiler() if sys.platform in ("darwin", "win32") else CCompiler("gcc", shutil.which("cc"), "Linux")
        for tool in TOOLS:
            extra = ""
            if tool != "mouse":
                extra += 'if (!strcmp(argv[1],"interval")) {printf("%u",interval_arg(argv[2],100,1000));return 0;}\n'
            if tool in ("volume", "mouse"):
                extra += '''if (!strcmp(argv[1],"input")) {
    int pipes[2];assert(pipe(pipes)==0);assert(dup2(pipes[0],STDIN_FILENO)>=0);close(pipes[0]);
    char out[256];assert(input_line(out,sizeof(out))==0);
    assert(write(pipes[1],"par",3)==3);assert(input_line(out,sizeof(out))==0);
    assert(write(pipes[1],"tial\\r\\nsecond\\n",13)==13);
    assert(input_line(out,sizeof(out))==1 && !strcmp(out,"partial"));
    assert(input_line(out,sizeof(out))==1 && !strcmp(out,"second"));
    assert(write(pipes[1],"12345\\n",6)==6);assert(input_line(out,5)==-2);
    char large[1001];memset(large,'x',1000);large[1000]='\\n';
    assert(write(pipes[1],large,sizeof(large))==(int)sizeof(large));assert(input_line(out,sizeof(out))==-2);
    assert(write(pipes[1],"ok\\n",3)==3);assert(input_line(out,sizeof(out))==1 && !strcmp(out,"ok"));
    close(pipes[1]);assert(input_line(out,sizeof(out))==-1);return 0;
}\n'''
            if tool == "screen":
                extra += '''if (!strcmp(argv[1],"base64")) {
    size_t n=(size_t)strtoul(argv[2],NULL,10);assert(n<=65539);
    unsigned char bytes[65539];for(size_t i=0;i<n;i++) bytes[i]=(unsigned char)i;
    base64(bytes,n);return 0;
}\n'''
            if tool == "mouse":
                extra += '''if (!strcmp(argv[1],"state")) {
    held[0]=1;held[1]=0;held[2]=1;report_mouse(1,0.5,-0.25);return 0;
}\n'''
            if tool == "metrics":
                extra += '''if (!strcmp(argv[1],"cpu_delta")) {
    uint64_t total=0,idle=0;double value=-1;
    assert(!cpu_delta(100,50,&total,&idle,&value));
    assert(cpu_delta(200,75,&total,&idle,&value) && fabs(value-75)<0.001);
    assert(!cpu_delta(200,75,&total,&idle,&value));
    assert(!cpu_delta(10,5,&total,&idle,&value));
    assert(!cpu_delta(20,0,&total,&idle,&value));
    assert(!cpu_delta(30,50,&total,&idle,&value));
    assert(cpu_delta(40,50,&total,&idle,&value) && fabs(value-100)<0.001);
    assert(cpu_delta(50,60,&total,&idle,&value) && fabs(value)<0.001);
    return 0;
}\n'''
                extra += '''if (!strcmp(argv[1],"sensor")) {
    double value=-1;int ok=temperature_file(argv[2],&value);printf("%d %.2f",ok,value);return 0;
}\n'''
            if tool == "processes":
                extra += '''if (!strcmp(argv[1],"cpu")) {
    Process old={0};old.pid=10;old.birth=1;old.measured=1;old.cpu_ns=1000000000ULL;
    Process current=old;current.cpu_ns=3000000000ULL;append(&current);
    measure_cpu(&old,1,1000);assert(list[0].cpu_known && fabs(list[0].cpu_percent-200)<0.001);
    measure_cpu(&old,1,0);assert(!list[0].cpu_known);
    list[0].birth=2;measure_cpu(&old,1,1000);assert(!list[0].cpu_known);
    list[0].birth=1;list[0].cpu_ns=1;measure_cpu(&old,1,1000);assert(!list[0].cpu_known);
    list[0].cpu_ns=3000000000ULL;list[0].measured=0;measure_cpu(&old,1,1000);assert(!list[0].cpu_known);
    list[0].measured=1;old.measured=0;measure_cpu(&old,1,1000);assert(!list[0].cpu_known);
    old.measured=1;old.pid=99;measure_cpu(&old,1,1000);assert(!list[0].cpu_known);
    free(list);return 0;
}\n'''
                extra += '''if (!strcmp(argv[1],"tree")) {
    Process p={0};p.pid=1;p.birth=1;append(&p);
    p.pid=10;p.ppid=1;p.birth=10;p.rss=100*1048576ULL;p.ram_known=1;append(&p);
    p.pid=11;p.ppid=10;p.birth=11;p.rss=99*1048576ULL;append(&p);
    p.pid=20;p.ppid=1;p.birth=20;p.ram_known=0;p.cpu_known=1;p.cpu_percent=1;append(&p);
    p.pid=30;p.ppid=1;p.ram_known=p.cpu_known=0;p.rss=999*1048576ULL;p.cpu_percent=99;append(&p);
    qsort(list,length,sizeof(*list),compare_pid);select_tree(0,0,1,100);
    assert(list[0].include && !list[0].selected);assert(list[1].selected);
    assert(!list[2].include && list[3].selected && !list[4].include);
    assert(find(list,length,10)==&list[1] && find(list,length,999)==NULL);
    for(size_t i=0;i<length;i++) list[i].selected=list[i].include=0;
    select_tree(0,10,1,100);assert(list[1].selected && list[2].selected && !list[3].include);
    for(size_t i=0;i<length;i++) list[i].selected=list[i].include=0;
    select_tree(0,999,1,100);for(size_t i=0;i<length;i++) assert(!list[i].include);
    select_tree(1,0,1,100);for(size_t i=0;i<length;i++) assert(list[i].selected && list[i].include);
    list[1].birth=99;assert(parent_of(&list[2])==NULL);
    list[0].ppid=1;assert(parent_of(&list[0])==NULL);
    list[1].ppid=11;list[2].ppid=10;list[1].birth=list[2].birth=0;
    for(size_t i=0;i<length;i++) list[i].selected=list[i].include=0;
    select_tree(0,999,1,100); /* Cycles must be bounded, not hang traversal. */
    free(list);return 0;
}\n'''
            code = f'#define main tool_main\n#include "{(ROOT / "native" / (tool + ".c")).as_posix()}"\n#undef main\n#include <assert.h>\n' + '''
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#define pipe(p) _pipe(p,4096,_O_BINARY)
#define dup2 _dup2
#define write _write
#define close _close
#define STDIN_FILENO 0
#endif
int main(int argc,char **argv) {
    assert(argc>=2);
    if(!strcmp(argv[1],"cli")) return tool_main(argc-1,argv+1);
    if(!strcmp(argv[1],"number")) {
        double value=-1;int ok=number(argv[2],0,100,&value);printf("%d %.2f",ok,value);return 0;
    }
    if(!strcmp(argv[1],"json")) {
        const char text[]="quote\\\" slash\\\\ newline\\n tab\\t\\x01 \\xc3\\xa9 \\xf0\\x9f\\x98\\x80 invalid \\xc0\\xaf \\xed\\xa0\\x80 \\xf4\\x90\\x80\\x80 truncated \\xe2\\x82";
        json_string(text);return 0;
    }
    if(!strcmp(argv[1],"events")) {
        event_start("telemetry","fixture");puts("\\\"ok\\\":true}");
''' + ('failure("fixture","quoted \\\"error\\\"\\n",1);' if tool != "metrics" else 'event_start("telemetry","fixture");puts("\\\"ok\\\":true}");') + '''
        return 0;
    }
''' + extra + '''
    return 2;
}
'''
            if tool in ("volume", "mouse"):
                code = code.replace("close(pipes[0]);", "close(pipes[0]);\n#ifdef _WIN32\n    assert(SetStdHandle(STD_INPUT_HANDLE,(HANDLE)_get_osfhandle(0)));\n#endif\n")
            source, binary = Path(cls.tmp.name) / f"{tool}.c", Path(cls.tmp.name) / (tool + (".exe" if sys.platform == "win32" else ""))
            source.write_text(code)
            compiler.compile(source, binary, timeout=120, link_args=link_arguments(tool, compiler) if sys.platform in ("darwin", "win32") else ("-lm",))
            cls.binaries[tool] = binary

    def run_tool(self, tool, *args, input_text=None):
        return subprocess.run([str(self.binaries[tool]), *args], input=input_text, capture_output=True, text=True, timeout=2)

    def test_number_validation_in_each_native_tool(self):
        for tool in TOOLS:
            for value in ("0", "100", "42.5"):
                with self.subTest(tool=tool, value=value):
                    result = self.run_tool(tool, "number", value)
                    self.assertEqual(result.stdout, f"1 {float(value):.2f}")
            for value in ("", "nan", "inf", "-1", "101", "1e999", "1;stop", "50\nstop", "50extra"):
                with self.subTest(tool=tool, value=value):
                    self.assertEqual(self.run_tool(tool, "number", value).stdout, "0 -1.00")

    def test_json_escaping_valid_unicode_and_invalid_sequences_in_each_tool(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                decoded = json.loads(self.run_tool(tool, "json").stdout)
                self.assertTrue(decoded.startswith('quote" slash\\ newline\n tab\t\x01 é 😀 invalid '))
                self.assertIn("�", decoded)
                self.assertTrue(decoded.endswith("truncated ��"))

    def test_event_envelopes_sequence_timestamp_and_error_escaping_in_each_tool(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                events = [json.loads(line) for line in self.run_tool(tool, "events").stdout.splitlines()]
                self.assertEqual([event["seq"] for event in events], [0, 1])
                self.assertTrue(all(event["timestamp_ms"] > 0 and event["stream"] == "fixture" for event in events))
                if tool != "metrics":
                    self.assertEqual(events[1]["type"], "command_result")
                    self.assertFalse(events[1]["ok"])
                    self.assertEqual(events[1]["error"], 'quoted "error"\n')

    def test_integer_interval_boundaries_and_fraction_rejection(self):
        for tool in ("volume", "metrics", "processes", "screen"):
            for value in ("100", "1000"):
                with self.subTest(tool=tool, value=value):
                    self.assertEqual(self.run_tool(tool, "interval", value).stdout, value)
            for value in ("99", "1001", "100.5", "nan", "", "1e999"):
                with self.subTest(tool=tool, value=value):
                    self.assertEqual(self.run_tool(tool, "interval", value).returncode, 2)

    def test_input_partial_writes_crlf_overflow_capacity_and_eof(self):
        for tool in ("volume", "mouse"):
            with self.subTest(tool=tool):
                result = self.run_tool(tool, "input")
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_tool_rejects_unknown_or_incomplete_cli_options(self):
        invalid = {"volume": [("--bad",), ("--interval",), ("--interval", "0")],
                   "mouse": [("--bad",), ("--speed",), ("--speed", "nan"), ("--speed", "0"), ("--speed", "5001")],
                   "metrics": [("--bad",), ("--temperature-file",), ("--interval", "0")],
                   "processes": [("--bad",), ("--pid",), ("--pid", "0"), ("--all", "--pid", "1"), ("--min-cpu", "nan"), ("--min-ram-mb", "-1")],
                   "screen": [("--bad",), ("--max-width",), ("--max-width", "63"), ("--max-width", "1921"), ("--interval", "199")]}
        for tool, cases in invalid.items():
            for args in cases:
                with self.subTest(tool=tool, args=args):
                    self.assertEqual(self.run_tool(tool, "cli", *args).returncode, 2)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Checks denied OS operations via the Linux fallback")
    def test_volume_commands_and_missing_device_are_structured_failures(self):
        commands = "set -1\nset 101\nset nan\nset 50 extra\nset 50\nstop\n"
        result = self.run_tool("volume", "cli", input_text=commands)
        self.assertEqual(result.returncode, 0)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        acknowledgements = [event for event in events if event["type"] == "command_result"]
        self.assertEqual(len(acknowledgements), 5)
        self.assertTrue(all(not event["ok"] for event in events))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Checks denied OS operations via the Linux fallback")
    def test_mouse_denies_invalid_or_unpermitted_input_and_reports_button_state(self):
        commands = "move 2 0\nmove nan 0\nmove 0.5 -0.5\nbutton unknown down\nbutton left down\nscroll 1.5\nscroll 1\nstop\n"
        result = self.run_tool("mouse", "cli", input_text=commands)
        self.assertEqual(result.returncode, 0)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        acknowledgements = [event for event in events if event["type"] == "command_result"]
        self.assertEqual(len(acknowledgements), 7)
        self.assertTrue(all(not event["ok"] for event in acknowledgements))
        state = json.loads(self.run_tool("mouse", "state").stdout)
        self.assertEqual(state["data"], {"joystick_x": 0.5, "joystick_y": -0.25, "buttons": [True, False, True], "position": None})

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux fallback never locks, so this is safe to run")
    def test_mouse_lock_command_is_exact_and_denied_where_unsupported(self):
        result = self.run_tool("mouse", "cli", input_text="lock\nlock now\nlocks\nstop\n")
        acknowledgements = [event for event in map(json.loads, result.stdout.splitlines()) if event["type"] == "command_result"]
        self.assertEqual(len(acknowledgements), 3)
        self.assertTrue(all(not event["ok"] for event in acknowledgements))

    def test_mouse_lock_uses_the_platform_session_lock_apis(self):
        source = (ROOT / "native" / "mouse.c").read_text()
        self.assertIn("LockWorkStation()", source)
        self.assertIn("SACLockScreenImmediate", source)
        self.assertIn('!strcmp(line,"lock")', source)

    def test_metrics_sensor_limits_missing_empty_stale_and_partial_telemetry(self):
        self.assertEqual(self.run_tool("metrics", "cpu_delta").returncode, 0)
        path = Path(self.tmp.name) / "sensor.txt"
        for value, available in (("42.5\n", True), ("0.01", True), ("149.99", True), ("0", False), ("150", False), ("nan", False), ("", False), ("text", False)):
            with self.subTest(value=value):
                path.write_text(value)
                self.assertEqual(self.run_tool("metrics", "sensor", str(path)).stdout.startswith("1 "), available)
        path.write_text("42.5\n")
        if sys.platform.startswith("linux"):
            result = self.run_tool("metrics", "cli", "--once", "--temperature-file", str(path))
            data = json.loads(result.stdout)["data"]
            self.assertEqual(data["cpu_temperature_c"], 42.5)
            self.assertEqual(data["temperature_source"], "local_sensor_file")
            self.assertIsNone(data["ram"])
        os.utime(path, (1, 1))
        self.assertTrue(self.run_tool("metrics", "sensor", str(path)).stdout.startswith("0 "))
        path.unlink()
        self.assertTrue(self.run_tool("metrics", "sensor", str(path)).stdout.startswith("0 "))
        if sys.platform.startswith("linux"):
            data = json.loads(self.run_tool("metrics", "cli", "--once").stdout)["data"]
            self.assertIsNone(data["cpu_temperature_c"])
            self.assertIn("No readable", data["temperature_status"])

    def test_process_selection_unknown_resources_boundaries_pid_reuse_and_cycles(self):
        result = self.run_tool("processes", "tree")
        self.assertEqual(result.returncode, 0, result.stderr)
        if sys.platform.startswith("linux"):
            missing = self.run_tool("processes", "cli", "--once")
            self.assertEqual(missing.returncode, 1)
            self.assertEqual(json.loads(missing.stdout)["type"], "error")

    def test_process_cpu_sampling_multicore_percent_zero_interval_reused_pid_and_missing_measurements(self):
        result = self.run_tool("processes", "cpu")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_screen_base64_padding_all_bytes_multiple_buffers_and_permission_failure(self):
        for size in (0, 1, 2, 3, 4, 8191, 8192, 8193, 16385, 65539):
            with self.subTest(size=size):
                result = self.run_tool("screen", "base64", str(size))
                self.assertEqual(result.stdout, base64.b64encode(bytes(index % 256 for index in range(size))).decode())
        if sys.platform.startswith("linux"):
            result = self.run_tool("screen", "cli", "--once")
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)["ok"])
