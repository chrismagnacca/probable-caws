"""Unit tests for harness/claude_runner.py: pure stream-json parsing and SessionResult
construction. No real `claude` subprocess is ever spawned here."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import claude_runner  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "stream_json_sample.jsonl"


def _read_fixture_lines() -> list:
    with open(FIXTURE_PATH, "rb") as f:
        return f.readlines()


class ParseLineTests(unittest.TestCase):
    def test_valid_json_object_line(self):
        obj = claude_runner.parse_line(b'{"type": "result", "is_error": false}\n')
        self.assertEqual(obj, {"type": "result", "is_error": False})

    def test_garbage_line_is_ignored(self):
        self.assertIsNone(claude_runner.parse_line(b"not json at all {{{\n"))

    def test_torn_json_line_is_ignored(self):
        self.assertIsNone(claude_runner.parse_line(b'{"type": "result", "is_er'))

    def test_empty_line_is_ignored(self):
        self.assertIsNone(claude_runner.parse_line(b""))
        self.assertIsNone(claude_runner.parse_line(b"\n"))
        self.assertIsNone(claude_runner.parse_line(b"   \n"))

    def test_json_scalar_is_ignored(self):
        # A bare JSON scalar (not an object) should be tolerated, not raise.
        self.assertIsNone(claude_runner.parse_line(b"42\n"))
        self.assertIsNone(claude_runner.parse_line(b'"just a string"\n'))

    def test_undecodable_bytes_are_ignored(self):
        self.assertIsNone(claude_runner.parse_line(b"\xff\xfe\x00\x01"))


class ParseResultEventTests(unittest.TestCase):
    def test_fixture_yields_the_result_event(self):
        lines = _read_fixture_lines()
        result_event = claude_runner.parse_result_event(lines)
        self.assertIsNotNone(result_event)
        self.assertEqual(result_event["type"], "result")
        self.assertEqual(result_event["session_id"], "sess_abc123")
        self.assertEqual(result_event["total_cost_usd"], 0.42)

    def test_tolerates_torn_and_garbage_lines_interspersed(self):
        lines = _read_fixture_lines()
        noisy = (
            [b"\n", b"not json\n", b'{"type": "assistant", "message": "trunc'] +
            lines +
            [b"garbage after result\n", b'{"type": "unknown_future_type"}\n']
        )
        result_event = claude_runner.parse_result_event(noisy)
        self.assertIsNotNone(result_event)
        self.assertEqual(result_event["session_id"], "sess_abc123")

    def test_no_result_event_returns_none(self):
        lines = [l for l in _read_fixture_lines() if b'"type":"result"' not in l]
        self.assertIsNone(claude_runner.parse_result_event(lines))


class BuildSessionResultTests(unittest.TestCase):
    def test_every_field_from_fixture(self):
        lines = _read_fixture_lines()
        result = claude_runner.build_session_result(
            session_dir="/tmp/some/session/dir",
            lines=lines,
            wall_s=184.0,
            returncode=0,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_reason, "ok")
        self.assertEqual(result.session_id, "sess_abc123")
        self.assertEqual(result.session_dir, "/tmp/some/session/dir")
        self.assertAlmostEqual(result.cost_usd, 0.42)
        self.assertEqual(result.input_tokens, 1200)
        self.assertEqual(result.output_tokens, 5300)
        self.assertEqual(result.cache_read_tokens, 41000)
        self.assertEqual(result.cache_creation_tokens, 900)
        self.assertEqual(result.num_turns, 12)
        self.assertEqual(result.wall_s, 184.0)
        self.assertEqual(result.final_text, "final text")

    def test_is_error_true_classified_as_error(self):
        lines = [
            b'{"type":"result","subtype":"error","is_error":true,"session_id":"s1",'
            b'"num_turns":1,"total_cost_usd":0.01,"usage":{},"result":"boom"}\n'
        ]
        result = claude_runner.build_session_result("dir", lines, wall_s=5.0, returncode=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_reason, "error")

    def test_nonzero_returncode_classified_as_error(self):
        lines = [
            b'{"type":"result","is_error":false,"session_id":"s1","total_cost_usd":0.0,'
            b'"usage":{},"result":""}\n'
        ]
        result = claude_runner.build_session_result("dir", lines, wall_s=1.0, returncode=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_reason, "error")

    def test_missing_result_event_is_no_result(self):
        result = claude_runner.build_session_result("dir", [b"only garbage\n"], wall_s=1.0, returncode=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_reason, "no_result")
        self.assertEqual(result.session_id, "")
        self.assertEqual(result.cost_usd, 0.0)
        self.assertEqual(result.input_tokens, 0)

    def test_forced_timeout_overrides_classification(self):
        lines = _read_fixture_lines()  # would otherwise parse as a clean "ok"
        result = claude_runner.build_session_result(
            "dir", lines, wall_s=999.0, returncode=None, forced_exit_reason="timeout",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_reason, "timeout")

    def test_forced_killed_overrides_classification(self):
        result = claude_runner.build_session_result(
            "dir", [], wall_s=3.0, returncode=None, forced_exit_reason="killed",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_reason, "killed")

    def test_missing_usage_fields_default_to_zero(self):
        lines = [
            b'{"type":"result","is_error":false,"session_id":"s1","total_cost_usd":0.0,'
            b'"result":""}\n'  # no "usage" key at all
        ]
        result = claude_runner.build_session_result("dir", lines, wall_s=1.0, returncode=0)
        self.assertTrue(result.ok)
        self.assertEqual(result.input_tokens, 0)
        self.assertEqual(result.output_tokens, 0)
        self.assertEqual(result.cache_read_tokens, 0)
        self.assertEqual(result.cache_creation_tokens, 0)


class FailureClassificationTests(unittest.TestCase):
    def test_is_infra_failure_true_for_non_ok(self):
        for reason in ("timeout", "error", "killed", "no_result"):
            result = claude_runner.SessionResult(
                ok=False, exit_reason=reason, session_id="", session_dir="", cost_usd=0.0,
                input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0,
                num_turns=0, wall_s=0.0, final_text="",
            )
            self.assertTrue(claude_runner.is_infra_failure(result))

    def test_is_infra_failure_false_for_ok(self):
        result = claude_runner.SessionResult(
            ok=True, exit_reason="ok", session_id="s", session_dir="", cost_usd=0.0,
            input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0,
            num_turns=1, wall_s=1.0, final_text="",
        )
        self.assertFalse(claude_runner.is_infra_failure(result))


class BackoffDelayTests(unittest.TestCase):
    def test_backoff_sequence_matches_30_120_480(self):
        self.assertEqual(claude_runner.backoff_delay_s(0, 30), 30.0)
        self.assertEqual(claude_runner.backoff_delay_s(1, 30), 120.0)
        self.assertEqual(claude_runner.backoff_delay_s(2, 30), 480.0)

    def test_custom_base(self):
        self.assertEqual(claude_runner.backoff_delay_s(0, 10), 10.0)
        self.assertEqual(claude_runner.backoff_delay_s(1, 10), 40.0)


class BuildCommandTests(unittest.TestCase):
    def test_command_shape(self):
        cmd = claude_runner.build_command("claude-sonnet-5", 150)
        self.assertEqual(cmd[0], "claude")
        self.assertIn("-p", cmd)
        self.assertIn("--output-format", cmd)
        self.assertIn("stream-json", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("claude-sonnet-5", cmd)
        self.assertIn("--max-turns", cmd)
        self.assertIn("150", cmd)
        self.assertIn("--permission-mode", cmd)
        self.assertIn("acceptEdits", cmd)


class RunnerInterfaceTests(unittest.TestCase):
    def test_auth_mode_follows_api_key_env(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            self.assertEqual(claude_runner.auth_mode(), "api_key")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(claude_runner.auth_mode(), "subscription")

    def test_doctor_checks_shape(self):
        for name, ok, message in claude_runner.doctor_checks():
            self.assertIsInstance(name, str)
            self.assertIsInstance(ok, bool)
            self.assertIsInstance(message, str)
            self.assertTrue(message)


if __name__ == "__main__":
    unittest.main()
