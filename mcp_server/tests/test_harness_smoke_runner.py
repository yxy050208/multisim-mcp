"""Unit tests for the isolated official Harness smoke-test runner."""

from __future__ import annotations

import importlib.util
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "smoke_deepseek_harness.py"
SPEC = importlib.util.spec_from_file_location("harness_smoke_runner", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class HarnessSmokeRunnerTest(unittest.TestCase):
    def test_pnpm_command_pins_package_version(self) -> None:
        with patch.object(smoke.shutil, "which", return_value="pnpm"):
            command = smoke._pnpm_command("@deepseek-ai/dsh", "0.2.1-rc.2", "--version")
        self.assertEqual(
            command,
                ["pnpm", "dlx", "@deepseek-ai/dsh@0.2.1-rc.2", "--version"],
        )

    def test_pnpm_command_fails_when_pnpm_is_missing(self) -> None:
        with patch.object(smoke.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "pnpm is unavailable"):
                smoke._pnpm_command("@deepseek-ai/dsh", "0.2.1-rc.2", "--version")

    def test_process_group_options_match_platform(self) -> None:
        options = smoke._process_group_options()
        if smoke.os.name == "nt":
            self.assertEqual(
                options, {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            )
        else:
            self.assertEqual(options, {"start_new_session": True})

    def test_tail_is_bounded_and_utf8_tolerant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "output.log"
            log.write_bytes(b"prefix-" + b"x" * 32 + b"-tail\xff")
            result = smoke._tail(log, limit=12)
        self.assertLessEqual(len(result), 12)
        self.assertIn("tail", result)

    def test_web_url_from_log_accepts_the_tokenized_dsh_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "dsh.stdout.log"
            log.write_text(
                "dsh web: http://127.0.0.1:60475/?token=abc123\n",
                encoding="utf-8",
            )
            self.assertEqual(
                smoke._web_url_from_log(log, 60475),
                "http://127.0.0.1:60475/?token=abc123",
            )
            self.assertIsNone(smoke._web_url_from_log(log, 60476))

    def test_wait_for_web_uses_tokenized_url(self) -> None:
        process = Mock()
        process.poll.return_value = None
        response = Mock(status=200)
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "dsh.stdout.log"
            url = "http://127.0.0.1:60475/?token=abc123"
            log.write_text(f"dsh web: {url}\n", encoding="utf-8")
            with patch.object(smoke.urllib.request, "urlopen", return_value=context) as urlopen:
                ready, detail = smoke._wait_for_web(
                    process, 60475, 1, stdout_path=log
                )
        self.assertTrue(ready)
        self.assertEqual(detail, "HTTP 200")
        urlopen.assert_called_once_with(url, timeout=2)

    def test_stop_process_escalates_after_timeout(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("dsh", 10), 0]
        if smoke.os.name == "nt":
            with patch.object(smoke.subprocess, "run") as taskkill:
                smoke._stop_process(process)
            process.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
            taskkill.assert_called_once()
        else:
            with patch.object(smoke.os, "killpg") as kill_group:
                smoke._stop_process(process)
            self.assertEqual(
                kill_group.call_args_list,
                [
                    call(process.pid, signal.SIGTERM),
                    call(process.pid, signal.SIGKILL),
                ],
            )


if __name__ == "__main__":
    unittest.main()
