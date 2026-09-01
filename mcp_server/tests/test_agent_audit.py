"""Tests for privacy-bounded, atomic agent audit artifacts."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.agent_audit import AgentAuditTrail, text_fingerprint


class AgentAuditTrailTest(unittest.TestCase):
    def test_successful_audit_is_bounded_private_and_refuses_overwrite(self) -> None:
        secret_prompt = "private prompt that must not be persisted"
        assistant_answer = "private model answer that must not be persisted"
        trail = AgentAuditTrail(
            "model-diagnose",
            {
                "design": {
                    "design_id": "divider-v1",
                    "source_netlist_recorded": False,
                }
            },
        )
        trail.record(
            "model_round_completed",
            {
                "round": 1,
                "assistant_content": text_fingerprint(assistant_answer),
            },
        )
        trail.record(
            "tool_call_validated",
            {
                "call_id": "call_1",
                "tool_name": "eda_inspect_net",
                "arguments": {"net": "out"},
            },
        )
        trail.succeed({"rounds": 1, "tool_call_count": 1})

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "audit.json"
            published = trail.write(str(output))
            content = output.read_text(encoding="utf-8")
            payload = json.loads(content)
            with self.assertRaises(FileExistsError):
                trail.write(str(output))
            replaced = trail.write(str(output), overwrite=True)

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["event_count"], 2)
        self.assertFalse(payload["privacy"]["prompt_content_recorded"])
        self.assertFalse(payload["privacy"]["assistant_content_recorded"])
        self.assertEqual(
            payload["events"][1]["details"]["arguments"], {"net": "out"}
        )
        self.assertNotIn(secret_prompt, content)
        self.assertNotIn(assistant_answer, content)
        self.assertEqual(published["sha256"], replaced["sha256"])
        self.assertFalse(published["content_recorded"])

    def test_failed_audit_records_sanitized_error_without_traceback(self) -> None:
        trail = AgentAuditTrail("model-diagnose", {})
        trail.record("run_started", {"max_rounds": 2})
        trail.fail(RuntimeError("bounded failure"))
        payload = trail.to_dict()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["type"], "RuntimeError")
        self.assertFalse(payload["error"]["message_recorded"])
        self.assertEqual(
            payload["error"]["message"], text_fingerprint("bounded failure")
        )
        self.assertNotIn("bounded failure", json.dumps(payload))
        self.assertNotIn("traceback", json.dumps(payload).lower())
        with self.assertRaises(RuntimeError):
            trail.record("late", {})

    def test_rejects_nonfinite_or_oversized_event_details(self) -> None:
        trail = AgentAuditTrail("model-diagnose", {})
        with self.assertRaisesRegex(ValueError, "non-finite"):
            trail.record("bad", {"value": math.inf})
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            trail.record("large", {"value": "x" * (64 * 1024)})


if __name__ == "__main__":
    unittest.main()
