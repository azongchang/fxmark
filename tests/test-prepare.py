#!/usr/bin/env python3
"""No root/device needed: exercise process lifecycle and exact prepared trees."""
import os
import ast
import ctypes
import signal
import shlex
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PrepareTests(unittest.TestCase):
    def test_mkdir_errors_and_literal_names(self):
        with tempfile.TemporaryDirectory(prefix="fxmark-mkdir-") as tmp:
            libpath = Path(tmp) / "mkdir.so"
            subprocess.run(["cc", "-shared", "-fPIC", str(ROOT / "src/util.c"),
                            "-o", str(libpath)], check=True)
            lib = ctypes.CDLL(str(libpath), use_errno=True)
            lib.mkdir_p.argtypes = [ctypes.c_char_p]
            lib.mkdir_p.restype = ctypes.c_int
            target = Path(tmp) / "literal ; space" / "nested"
            self.assertEqual(lib.mkdir_p(os.fsencode(target)), 0)
            self.assertEqual(lib.mkdir_p(os.fsencode(target)), 0)
            self.assertTrue(target.is_dir())
            regular = Path(tmp) / "file"
            regular.touch()
            self.assertNotEqual(lib.mkdir_p(os.fsencode(regular / "child")), 0)
            self.assertNotEqual(lib.mkdir_p(b""), 0)
            self.assertNotEqual(lib.mkdir_p(b"a" * 4096), 0)

    def test_runner_large_output_and_timeout(self):
        tree = ast.parse((ROOT / "bin/run-fxmark.py").read_text())
        runner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Runner")
        method = next(n for n in runner.body if isinstance(n, ast.FunctionDef) and n.name == "exec_cmd")
        scope = dict(subprocess=subprocess, tempfile=tempfile, os=os, signal=signal, sys=sys)
        exec(compile(ast.Module(body=[method], type_ignores=[]), "runner-method", "exec"), scope)
        run = scope["exec_cmd"]
        cmd = shlex.quote(sys.executable) + " -c " + shlex.quote(
            "import os; os.write(1, b'x'*262144); os.write(2, b'y'*262144)")
        p = run(None, cmd, out=subprocess.PIPE, timeout=5)
        self.assertEqual(p.returncode, 0)
        with p.stdout:
            data = p.stdout.read()
        self.assertEqual(data.count(b"x"), 262144)
        self.assertEqual(data.count(b"y"), 262144)
        p = run(None, "sleep 10", out=subprocess.PIPE, timeout=0.1)
        self.assertNotEqual(p.returncode, 0)
        p.stdout.close()

    def test_process_barrier_and_errors(self):
        with tempfile.TemporaryDirectory(prefix="fxmark-test-") as tmp:
            exe = str(Path(tmp) / "lifecycle")
            subprocess.run(["cc", "-D_GNU_SOURCE", "-O0", "-g",
                            "-I" + str(ROOT / "src"),
                            str(ROOT / "tests/parallel-init.c"),
                            str(ROOT / "src/bench.c"), "-Wl,--wrap=fork",
                            "-Wl,--wrap=clock_gettime", "-o", exe], check=True)
            for mode, parallel in [(0, 0)] + [(m, 1) for m in range(7)]:
                subprocess.run([exe, str(mode), str(parallel)], timeout=10, check=True)

    def test_partitioned_tree(self):
        # Both workloads prepare the same 8^5 files, regardless of CPU count.
        # Include ncore > 8 to exercise subdivision below the root branches.
        for bench in ("MRPM", "MRPH"):
            for parallel, cores in ((0, 1), (1, 4), (1, 12)):
                with tempfile.TemporaryDirectory(prefix="fxmark tree ") as tmp:
                    subprocess.run([str(ROOT / "bin/fxmark"), "--type", bench,
                                    "--ncore", str(cores), "--nbg", "0", "--duration", "1",
                                    "--directio", "0", "--root", tmp],
                                   env={**os.environ, "FXMARK_PARALLEL_INIT": str(parallel)},
                                   timeout=30, check=True, stdout=subprocess.DEVNULL)
                    files = {str(p.relative_to(tmp)) for p in Path(tmp).rglob("*") if p.is_file()}
                    expected = {f"{i}/{j}/{k}/{l}/{m}" for i in range(8) for j in range(8)
                                for k in range(8) for l in range(8) for m in range(8)}
                    self.assertEqual(files, expected)

if __name__ == "__main__":
    unittest.main()
