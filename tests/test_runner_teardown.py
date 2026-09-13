#!/usr/bin/env python3
"""Regression tests for strict fxmark teardown handling."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


FXMARK_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = FXMARK_ROOT / "bin" / "run-fxmark.py"
PROJECT_ROOT = FXMARK_ROOT.parents[1]
os.environ.setdefault("SSRFS_ROOT_DIR", str(PROJECT_ROOT))
os.environ.setdefault("SSRFS_MOUNT_POINT", str(PROJECT_ROOT / "mnt"))
sys.path.insert(0, str(FXMARK_ROOT / "bin"))
SPEC = importlib.util.spec_from_file_location("fxmark_runner", RUNNER_PATH)
runner_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner_module)


class StrictTeardownTest(unittest.TestCase):
    def bare_runner(self):
        runner = runner_module.Runner.__new__(runner_module.Runner)
        runner.active_dev_path = None
        runner.umount_hook = []
        runner.failures = 0
        runner.teardown_failed = False
        runner.dev_null = None
        runner.log_fd = SimpleNamespace(closed=True)
        runner.log = lambda message: None
        return runner

    def test_unmounted_target_is_already_clean(self):
        runner = self.bare_runner()
        runner.exec_cmd = mock.Mock()
        with mock.patch.object(os.path, "ismount", return_value=False):
            self.assertTrue(runner.umount("/mnt/fxmark"))
        runner.exec_cmd.assert_not_called()

    def test_timeout_is_failure_without_lazy_detach(self):
        runner = self.bare_runner()
        calls = []
        runner.exec_cmd = lambda command, output: (
            calls.append(command) or SimpleNamespace(returncode=124)
        )
        with mock.patch.object(os.path, "ismount", return_value=True):
            self.assertFalse(runner.umount("/mnt/fxmark"))
        self.assertTrue(runner.teardown_failed)
        self.assertEqual(runner.failures, 1)
        self.assertEqual(calls, ["sudo timeout 60 umount /mnt/fxmark"])

    def test_mount_refuses_to_format_after_teardown_failure(self):
        runner = self.bare_runner()
        runner.HOWTO_MOUNT = {"f2fs": object()}
        runner.umount = lambda where: False
        runner.exec_cmd = mock.Mock()
        self.assertFalse(runner.mount("nvme", "f2fs", "/mnt/fxmark"))
        runner.exec_cmd.assert_not_called()

    def test_no_lazy_unmount_remains_in_runner_or_wrapper(self):
        wrapper = FXMARK_ROOT.parents[1] / "utils/evals/microbench/run-fxmark.sh"
        self.assertNotIn("umount -l", RUNNER_PATH.read_text())
        self.assertNotIn("umount -l", wrapper.read_text())


if __name__ == "__main__":
    unittest.main()
