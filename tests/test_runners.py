"""Unit tests for the codex/gemini/kimi runner parsers (CONTRACTS.md section 4b):
each fixture is fed through build_session_result and every SessionResult field is
asserted. No real CLI subprocess is ever spawned here."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import codex_runner, gemini_runner, kimi_runner  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _lines(name: str) -> list:
    with open(FIXTURES / name, "rb") as f:
        return f.readlines()


class CodexParserTests(unittest.TestCase):
    def test_full_fixture_fields(self):
        r = codex_runner.build_session_result("0007-generator-F003", _lines("codex_stream_sample.jsonl"), 42.5, 0)
        self.assertTrue(r.ok)
        self.assertEqual(r.exit_reason, "ok")
        self.assertEqual(r.session_id, "01a01ff6-4b65-7b11-bc38-95a9bd38cb9f")
        self.assertEqual(r.session_dir, "0007-generator-F003")
        self.assertEqual(r.cost_usd, 0.0)
        self.assertEqual(r.input_tokens, 14091)
        self.assertEqual(r.output_tokens, 5)
        self.assertEqual(r.cache_read_tokens, 9984)
        self.assertEqual(r.cache_creation_tokens, 120)
        self.assertEqual(r.num_turns, 1)
        self.assertEqual(r.wall_s, 42.5)
        self.assertEqual(r.final_text, "Feature implemented; handoff written.")

    def test_no_turn_completed_is_no_result(self):
        lines = [b'{"type":"thread.started","thread_id":"t1"}\n', b'{"type":"turn.started"}\n']
        r = codex_runner.build_session_result("d", lines, 1.0, 0)
        self.assertEqual(r.exit_reason, "no_result")
        self.assertFalse(r.ok)

    def test_error_event_and_nonzero_exit(self):
        lines = _lines("codex_stream_sample.jsonl")
        r = codex_runner.build_session_result("d", lines + [b'{"type":"error","message":"boom"}\n'], 1.0, 0)
        self.assertEqual(r.exit_reason, "error")
        r2 = codex_runner.build_session_result("d", lines, 1.0, 2)
        self.assertEqual(r2.exit_reason, "error")

    def test_forced_reason_and_session_id_fallback(self):
        r = codex_runner.build_session_result("0009-evaluator-F001", [], 9.0, None, forced_exit_reason="timeout")
        self.assertEqual(r.exit_reason, "timeout")
        self.assertEqual(r.session_id, "0009-evaluator-F001")

    def test_build_command_prompt_via_stdin_dash(self):
        cmd = codex_runner.build_command("gpt-5-codex", 40)
        self.assertEqual(cmd[-1], "-")
        self.assertIn("--json", cmd)
        self.assertIn("workspace-write", cmd)


class GeminiParserTests(unittest.TestCase):
    def test_full_fixture_fields(self):
        r = gemini_runner.build_session_result("0003-planner-F000", _lines("gemini_stream_sample.jsonl"), 30.0, 0)
        self.assertTrue(r.ok)
        self.assertEqual(r.exit_reason, "ok")
        self.assertEqual(r.session_id, "gm-7f3a2b")
        self.assertEqual(r.session_dir, "0003-planner-F000")
        self.assertEqual(r.cost_usd, 0.0135)
        self.assertEqual(r.input_tokens, 8200)
        self.assertEqual(r.output_tokens, 410)
        self.assertEqual(r.cache_read_tokens, 5100)
        self.assertEqual(r.cache_creation_tokens, 0)
        self.assertEqual(r.num_turns, 0)
        self.assertEqual(r.wall_s, 30.0)
        self.assertEqual(r.final_text, "Feature implemented; handoff written.")

    def test_delta_only_stream_concatenates(self):
        lines = [
            b'{"type":"init","session_id":"s"}\n',
            b'{"type":"message","role":"assistant","content":"a ","delta":true}\n',
            b'{"type":"message","role":"assistant","content":"b","delta":true}\n',
            b'{"type":"result","status":"success","stats":{"input_tokens":1,"output_tokens":1}}\n',
        ]
        r = gemini_runner.build_session_result("d", lines, 1.0, 0)
        self.assertEqual(r.final_text, "a b")
        self.assertTrue(r.ok)

    def test_missing_result_and_failed_status(self):
        r = gemini_runner.build_session_result("d", [b'{"type":"init","session_id":"s"}\n'], 1.0, 0)
        self.assertEqual(r.exit_reason, "no_result")
        r2 = gemini_runner.build_session_result(
            "d", [b'{"type":"result","status":"error","stats":{}}\n'], 1.0, 0)
        self.assertEqual(r2.exit_reason, "error")


class KimiParserTests(unittest.TestCase):
    def test_full_fixture_fields(self):
        r = kimi_runner.build_session_result("0005-generator-F002", _lines("kimi_stream_sample.jsonl"), 55.0, 0)
        self.assertTrue(r.ok)
        self.assertEqual(r.exit_reason, "ok")
        self.assertEqual(r.session_id, "km-19ab3c")
        self.assertEqual(r.session_dir, "0005-generator-F002")
        self.assertEqual(r.cost_usd, 0.0)
        self.assertEqual(r.input_tokens, 9300)
        self.assertEqual(r.output_tokens, 380)
        self.assertEqual(r.cache_read_tokens, 0)
        self.assertEqual(r.cache_creation_tokens, 0)
        self.assertEqual(r.num_turns, 0)
        self.assertEqual(r.wall_s, 55.0)
        self.assertEqual(r.final_text, "Feature implemented; handoff written.")

    def test_top_level_role_shape_also_parses(self):
        lines = [b'{"role":"assistant","content":"done","usage":{"input_tokens":10,"output_tokens":2}}\n']
        r = kimi_runner.build_session_result("d", lines, 1.0, 0)
        self.assertTrue(r.ok)
        self.assertEqual(r.final_text, "done")
        self.assertEqual(r.input_tokens, 10)

    def test_no_assistant_message_is_no_result(self):
        r = kimi_runner.build_session_result("0001-planner-F000", [], 1.0, 0)
        self.assertEqual(r.exit_reason, "no_result")
        self.assertEqual(r.session_id, "0001-planner-F000")

    def test_nonzero_exit_is_error(self):
        r = kimi_runner.build_session_result("d", _lines("kimi_stream_sample.jsonl"), 1.0, 3)
        self.assertEqual(r.exit_reason, "error")

    def test_prompt_travels_as_argument(self):
        cmd = kimi_runner.build_command("kimi-k2", 40, "do the thing")
        self.assertIn("do the thing", cmd)
        self.assertIn("--auto", cmd)


if __name__ == "__main__":
    unittest.main()
