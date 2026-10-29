import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_agent.cli import load_env


class CliTests(unittest.TestCase):
    def invoke(self, *args, env=None, cwd=None):
        return subprocess.run(
            [sys.executable, str(ROOT / "run.py"), *args],
            capture_output=True, text=True, encoding="utf-8",
            cwd=cwd or ROOT, env=env, timeout=20,
        )

    def test_offline_command_and_output_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            trace = Path(directory) / "trace.jsonl"
            process = self.invoke("ask", "报销期限是多少天？", "--mode", "mock",
                                  "--mock-scenario", "conflict",
                                  "--output", str(output), "--trace", str(trace), cwd=directory)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result, json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual(result["status"], "conflict")
            self.assertEqual(len(trace.read_text(encoding="utf-8").splitlines()), result["steps"])

    def test_context_input_file(self):
        process = self.invoke("ask", "--input", str(ROOT / "examples" / "context.input.json"), "--mode", "mock", "--mock-scenario", "context")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("15 分钟", json.loads(process.stdout)["answer"])

    def test_incomplete_has_exit_code_two(self):
        process = self.invoke("ask", "VPN怎么用？", "--mode", "mock", "--mock-scenario", "normal", "--max-steps", "1")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["status"], "incomplete")

    def test_missing_key_does_not_silently_use_mock(self):
        environment = dict(os.environ)
        environment.pop("KNOWLEDGE_TEST_MISSING_KEY", None)
        process = self.invoke("ask", "VPN怎么用？", "--api-key-env", "KNOWLEDGE_TEST_MISSING_KEY", env=environment)
        self.assertEqual(process.returncode, 3)
        self.assertEqual(json.loads(process.stderr)["status"], "error")
        self.assertFalse(process.stdout)

    def test_invalid_json_is_reported_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text("{invalid", encoding="utf-8")
            process = self.invoke("ask", "--input", str(path), "--mode", "mock", "--mock-scenario", "normal")
            self.assertEqual(process.returncode, 3)
            self.assertNotIn("Traceback", process.stderr)
            self.assertIn("JSON", json.loads(process.stderr)["error"])

    def test_history_invalid_role_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps({"question": "VPN怎么用？", "history": [{"role": [], "content": "历史"}]}), encoding="utf-8")
            process = self.invoke("ask", "--input", str(path), "--mode", "mock", "--mock-scenario", "normal")
            self.assertEqual(process.returncode, 3)
            self.assertNotIn("Traceback", process.stderr)

    def test_evaluation_generates_reviewable_files(self):
        with tempfile.TemporaryDirectory() as directory:
            process = self.invoke("evaluate", "--mode", "mock", "--case", "normal", "--case", "tool_recovery", "--output-dir", directory)
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads((Path(directory) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual((report["passed"], report["total"]), (2, 2))
            self.assertEqual(len(list(Path(directory).glob("*.input.json"))), 2)
            self.assertEqual(len(list(Path(directory).glob("*.output.json"))), 2)
            self.assertEqual(len(list(Path(directory).glob("*.trace.jsonl"))), 2)

    def test_unknown_case_returns_error(self):
        with tempfile.TemporaryDirectory() as directory:
            process = self.invoke("evaluate", "--mode", "mock", "--case", "not_a_case", "--output-dir", directory)
            self.assertEqual(process.returncode, 3)

    def test_mock_requires_explicit_scenario(self):
        process = self.invoke("ask", "报销期限是多少天？", "--mode", "mock")
        self.assertEqual(process.returncode, 3)
        self.assertIn("--mock-scenario", json.loads(process.stderr)["error"])

    def test_invalid_evaluation_cases_are_reported_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            for value in ([{}], [{"id": "normal", "question": "问题", "expected": []}]):
                path.write_text(json.dumps(value), encoding="utf-8")
                process = self.invoke("evaluate", "--mode", "mock", "--cases", str(path), "--output-dir", directory)
                self.assertEqual(process.returncode, 3)
                self.assertEqual(json.loads(process.stderr)["status"], "error")
                self.assertNotIn("Traceback", process.stderr)

    def test_env_file_is_not_executed_and_preserves_environment(self):
        key = "KNOWLEDGE_AGENT_TEST_VARIABLE"
        original = os.environ.get(key)
        try:
            os.environ[key] = "existing"
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / ".env"
                path.write_text(key + "=new\nKNOWLEDGE_AGENT_TEST_LITERAL='$(secret)'\n", encoding="utf-8")
                load_env(path)
                self.assertEqual(os.environ[key], "existing")
                self.assertEqual(os.environ["KNOWLEDGE_AGENT_TEST_LITERAL"], "$(secret)")
        finally:
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
            os.environ.pop("KNOWLEDGE_AGENT_TEST_LITERAL", None)


if __name__ == "__main__":
    unittest.main()
