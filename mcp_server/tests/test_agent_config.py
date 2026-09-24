import json
import tempfile
import unittest
from pathlib import Path

from tools.generate_agent_config import build_config


class AgentConfigTest(unittest.TestCase):
    def test_supported_clients_share_worker_shape(self):
        for client in ("qwen", "chatgpt", "clawcode", "workbody", "deepseek"):
            config = build_config(client, r"C:\Python32\python.exe", "repo", "work")
            self.assertEqual(config["mcpServers"]["multisim"]["command"][0], r"C:\Python32\python.exe")
            self.assertEqual(config["runtime"]["worker_python_bits"], 32)
            self.assertTrue(config["mcpServers"]["multisim"]["env"]["PYTHONPATH"].endswith("mcp_server"))

    def test_output_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = build_config("qwen", "python32", "repo", "work")
            Path(tmp, "config.json").write_text(json.dumps(value), encoding="utf-8")
