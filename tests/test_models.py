import io
import json
import socket
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from knowledge_agent.models import (
    MockModel, ModelError, OpenAICompatibleModel, ScriptedModel, SYSTEM_PROMPT, _connect_ipv4_first,
)


def state(observations=None, question="报销期限多久？", history=None):
    return {"question": question, "history": history or [], "observations": observations or [],
            "limits": {"max_steps": 12, "max_tool_calls": 8, "remaining_steps": 12, "remaining_tool_calls": 8}}


def observed(action, arguments, result):
    return {"step": 1, "action": action, "arguments": arguments, "result": result}


def provider_response(content=None, **updates):
    if content is None:
        content = {"action": "search", "arguments": {"query": "报销期限"}, "reason": "检索资料。"}
    payload = {"id": "chatcmpl-test123", "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(content, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
    payload.update(updates)
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.model = OpenAICompatibleModel("dummy-not-a-real-key")
        self.model._opener = Mock()

    def fail_code(self, code):
        with self.assertRaises(ModelError) as caught:
            self.model.decide(state())
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("dummy-not-a-real-key", str(caught.exception))

    def test_request_uses_system_prompt_current_state_and_json_mode(self):
        self.model._opener.open.return_value = provider_response()
        current = state(history=[{"role": "user", "content": "之前问的是费用"}])
        decision = self.model.decide(current)
        request = self.model._opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(payload["messages"][0]["content"], SYSTEM_PROMPT)
        self.assertEqual(json.loads(payload["messages"][1]["content"]), current)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(decision["action"], "search")
        self.assertEqual(self.model.last_usage["total_tokens"], 30)
        self.assertEqual(self.model.last_metadata["response_id"], "chatcmpl-test123")
        self.assertEqual(self.model.mode, "live")

    def test_explicit_endpoint_is_not_duplicated(self):
        model = OpenAICompatibleModel("test", base_url="https://example.test/v1/chat/completions/")
        self.assertEqual(model.endpoint, "https://example.test/v1/chat/completions")

    def test_local_http_allowed(self):
        model = OpenAICompatibleModel("test", base_url="http://127.0.0.1:8999/v1")
        self.assertEqual(model.endpoint, "http://127.0.0.1:8999/v1/chat/completions")

    def test_invalid_urls_rejected_without_echoing_input(self):
        for url in ("http://example.test", "https://user:secret@example.test", "file:///tmp/file",
                    "https://example.test?key=secret", "https://example.test#key=secret",
                    "https://example.test:99999", "https://exa mple.test", None):
            with self.subTest(url=url), self.assertRaises(ModelError) as caught:
                OpenAICompatibleModel("test", base_url=url)
            self.assertEqual(caught.exception.code, "invalid_base_url")
            self.assertNotIn("secret", str(caught.exception))

    def test_configuration_validation(self):
        for kwargs, code in (({"api_key": ""}, "missing_api_key"),
                             ({"api_key": "test", "model": " "}, "invalid_model"),
                             ({"api_key": "test", "timeout": 0}, "invalid_timeout"),
                             ({"api_key": "test", "timeout": True}, "invalid_timeout")):
            with self.subTest(kwargs=kwargs), self.assertRaises(ModelError) as caught:
                OpenAICompatibleModel(**kwargs)
            self.assertEqual(caught.exception.code, code)

    def test_http_error_never_exposes_provider_body(self):
        self.model._opener.open.side_effect = urllib.error.HTTPError(
            "https://example.test/", 401, "dummy-not-a-real-key", {}, io.BytesIO(b"dummy-not-a-real-key"))
        self.fail_code("http_401")
        self.assertEqual(self.model._opener.open.call_count, 1)

    def test_timeout(self):
        self.model._opener.open.side_effect = socket.timeout("dummy-not-a-real-key")
        self.fail_code("timeout")

    def test_wrapped_timeout(self):
        self.model._opener.open.side_effect = urllib.error.URLError(socket.timeout("private"))
        self.fail_code("timeout")

    def test_network_error(self):
        self.model._opener.open.side_effect = urllib.error.URLError("dummy-not-a-real-key")
        self.fail_code("network_error")

    def test_response_size_limit(self):
        self.model._opener.open.return_value = io.BytesIO(b" " * 1_048_577)
        self.fail_code("response_too_large")

    def test_non_json_response(self):
        self.model._opener.open.return_value = io.BytesIO(b"<html>provider unavailable</html>")
        self.fail_code("malformed_response")

    def test_invalid_response_shape(self):
        for payload in ([], {}, {"choices": []}, {"choices": [1]}, {"choices": [{"message": None}]}):
            with self.subTest(payload=payload):
                self.model._opener.open.return_value = io.BytesIO(json.dumps(payload).encode())
                self.fail_code("malformed_response")

    def test_refusal(self):
        self.model._opener.open.return_value = provider_response(choices=[{
            "finish_reason": "stop", "message": {"refusal": "denied", "content": None}}])
        self.fail_code("refused")

    def test_filtered_response(self):
        self.model._opener.open.return_value = provider_response(choices=[{
            "finish_reason": "content_filter", "message": {"content": "{}"}}])
        self.fail_code("refused")

    def test_truncation(self):
        self.model._opener.open.return_value = provider_response(choices=[{
            "finish_reason": "length", "message": {"content": "{"}}])
        self.fail_code("truncated")

    def test_tool_call_response_not_silently_accepted(self):
        self.model._opener.open.return_value = provider_response(choices=[{
            "finish_reason": "tool_calls", "message": {"content": None}}])
        self.fail_code("unsupported_finish")

    def test_malformed_decision(self):
        self.model._opener.open.return_value = provider_response(choices=[{
            "finish_reason": "stop", "message": {"content": "```json\n{}\n```"}}])
        self.fail_code("malformed_decision")

    def test_json_structure_is_left_to_core_validation(self):
        self.model._opener.open.return_value = provider_response(content=[])
        self.assertEqual(self.model.decide(state()), [])

    def test_core_can_recover_from_invalid_json_decision_structure(self):
        from knowledge_agent.agent import Agent
        from knowledge_agent.retrieval import KnowledgeBase

        corpus = Path(__file__).resolve().parents[1] / "data" / "knowledge_base.json"
        kb = KnowledgeBase(corpus)
        quote = kb.read("D03")["content"].split("。")[0] + "。"
        decisions = [[],
            {"action": "search", "arguments": {"query": "VPN"}, "reason": "修正格式后检索。"},
            {"action": "read", "arguments": {"doc_id": "D03"}, "reason": "读取原文。"},
            {"action": "finish", "arguments": {"status": "answered", "citations": [
                {"doc_id": "D03", "quote": quote}], "gaps": []}, "reason": "引用完整原文。"}]
        self.model._opener.open.side_effect = [provider_response(content=item) for item in decisions]
        result = Agent(self.model, kb).run("首次使用 VPN 怎么操作？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["trace"][0]["action"], "invalid_decision")
        self.assertEqual(result["trace"][1]["action"], "search")

    def test_usage_fields_are_filtered_and_reset(self):
        self.model._opener.open.return_value = provider_response(usage={
            "prompt_tokens": "private", "completion_tokens": True, "total_tokens": -1,
            "raw_prompt": "private"})
        self.model.decide(state())
        self.assertEqual(self.model.last_usage, {})
        self.model._opener.open.side_effect = socket.timeout()
        self.fail_code("timeout")
        self.assertNotIn("response_id", self.model.last_metadata)


class MockTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def run_scenario(self, scenario, question, data="knowledge_base.json", **options):
        from knowledge_agent.agent import Agent
        from knowledge_agent.retrieval import KnowledgeBase

        return Agent(MockModel(scenario), KnowledgeBase(self.root / "data" / data), **options).run(question)

    def test_scenario_is_required_and_unknown_scenario_is_rejected(self):
        with self.assertRaises(TypeError):
            MockModel()
        with self.assertRaises(ModelError) as caught:
            MockModel("not-an-existing-scenario")
        self.assertEqual(caught.exception.code, "unknown_mock_scenario")

    def test_label_explains_that_scenario_is_not_language_understanding(self):
        model = MockModel("normal")
        self.assertEqual(model.mode, "mock")
        self.assertEqual(model.name, "offline-scenario:normal")
        self.assertIn("不验证自然语言理解", model.validation_scope)
        self.assertEqual(model.decide(state(question="这个 VPN 首次使用怎么操作？"))["action"], "search")

    def test_all_acceptance_scenarios_are_available(self):
        cases = json.loads((self.root / "examples" / "cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]):
                model = MockModel(case.get("mock_scenario", case["id"]))
                self.assertEqual(model.scenario, case.get("mock_scenario", case["id"]))

    def test_alias_uses_same_workflow_but_has_explicit_requested_name(self):
        original, alias = MockModel("normal"), MockModel("tool_recovery")
        self.assertEqual(original.decide(state()), alias.decide(state()))
        self.assertEqual(alias.name, "offline-scenario:tool_recovery")

    def test_citations_are_loaded_from_changed_document_not_fixed_answers(self):
        result = self.run_scenario("changed_policy", "报销期限是多少天？", "changed_policy.json")
        self.assertEqual(result["status"], "answered")
        self.assertIn("45 天", result["answer"])
        self.assertNotIn("30 天", result["answer"])
        self.assertEqual(result["citations"][0]["doc_id"], "D01")

    def test_transient_tool_failure_retries_then_continues_scenario(self):
        result = self.run_scenario("tool_recovery", "首次使用企业 VPN 怎么操作？", failures={"read": 1})
        self.assertEqual(result["status"], "answered")
        self.assertEqual([event["action"] for event in result["trace"]], ["search", "read", "read", "finish"])
        self.assertIn("error", result["trace"][1]["result"])
        self.assertNotIn("error", result["trace"][2]["result"])

    def test_persistent_tool_failure_stops_with_real_obstacle(self):
        result = self.run_scenario("tool_failure", "首次使用企业 VPN 怎么操作？", failures={"read": -1})
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["citations"], [])
        self.assertIn("工具调用失败", result["gaps"][0])
        self.assertEqual(sum(event["action"] == "read" for event in result["trace"]), 2)

    def test_scenario_stops_when_expected_document_was_not_found(self):
        model = MockModel("normal")
        current = state(question="VPN")
        search = model.decide(current)
        current["observations"] = [observed("search", search["arguments"], {"hits": [], "total": 0})]
        with self.assertRaises(ModelError) as caught:
            model.decide(current)
        self.assertEqual(caught.exception.code, "mock_scenario_mismatch")

    def test_sentence_index_mismatch_does_not_fabricate_quote(self):
        model = MockModel("context")
        current = state()
        search = model.decide(current)
        current["observations"] = [observed("search", search["arguments"], {"hits": [{"doc_id": "D03"}]})]
        read = model.decide(current)
        current["observations"].append(observed("read", read["arguments"], {"doc_id": "D03", "content": "只有一句。"}))
        with self.assertRaises(ModelError) as caught:
            model.decide(current)
        self.assertEqual(caught.exception.code, "mock_scenario_mismatch")

    def test_unexpected_observation_is_reported_as_scenario_mismatch(self):
        model = MockModel("normal")
        with self.assertRaises(ModelError) as caught:
            model.decide(state([observed("read", {"doc_id": "D03"}, {"doc_id": "D03", "content": "原文。"})]))
        self.assertEqual(caught.exception.code, "mock_scenario_mismatch")

    def test_context_citation_keeps_semicolon_conditions(self):
        result = self.run_scenario("context", "那连续登录失败 3 次怎么办？")
        self.assertEqual(result["status"], "answered")
        self.assertIn("15 分钟", result["answer"])
        self.assertIn("IT 服务台", result["answer"])
        self.assertEqual(len(result["citations"]), 1)

    def test_scripted_model_copies_inputs_and_has_distinct_mode(self):
        decisions = [{"action": "finish", "arguments": {}, "reason": "test"}]
        model = ScriptedModel(decisions)
        decisions[0]["action"] = "bad"
        self.assertEqual(model.decide({})["action"], "finish")
        self.assertEqual(model.mode, "scripted")
        with self.assertRaises(ModelError) as caught:
            model.decide({})
        self.assertEqual(caught.exception.code, "script_exhausted")


class ConnectionTests(unittest.TestCase):
    addresses = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]

    def test_ipv4_is_tried_first_without_patching_global_socket(self):
        connection = Mock()
        original_factory = socket.create_connection
        with patch("knowledge_agent.models.socket.getaddrinfo", return_value=list(self.addresses)), \
                patch("knowledge_agent.models.socket.socket", return_value=connection) as factory:
            result = _connect_ipv4_first(("example.test", 443), timeout=10)
        self.assertIs(result, connection)
        self.assertEqual(factory.call_args.args[0], socket.AF_INET)
        connection.connect.assert_called_once_with(("127.0.0.1", 443))
        self.assertIs(socket.create_connection, original_factory)

    def test_ipv6_is_fallback_if_ipv4_fails(self):
        ipv4, ipv6 = Mock(), Mock()
        ipv4.connect.side_effect = OSError("IPv4 unavailable")
        with patch("knowledge_agent.models.socket.getaddrinfo", return_value=list(self.addresses)), \
                patch("knowledge_agent.models.socket.socket", side_effect=[ipv4, ipv6]):
            result = _connect_ipv4_first(("example.test", 443), timeout=10)
        self.assertIs(result, ipv6)
        ipv4.close.assert_called_once()
        ipv6.connect.assert_called_once_with(("::1", 443, 0, 0))

    def test_candidates_share_one_timeout_budget(self):
        clock = [0]
        ipv4, ipv6 = Mock(), Mock()

        def first_attempt(address):
            clock[0] = 7
            raise OSError("first route failed")

        def second_attempt(address):
            clock[0] = 8

        ipv4.connect.side_effect = first_attempt
        ipv6.connect.side_effect = second_attempt
        with patch("knowledge_agent.models.socket.getaddrinfo", return_value=list(self.addresses)), \
                patch("knowledge_agent.models.socket.socket", side_effect=[ipv4, ipv6]), \
                patch("knowledge_agent.models.time.monotonic", side_effect=lambda: clock[0]):
            result = _connect_ipv4_first(("example.test", 443), timeout=10)
        self.assertIs(result, ipv6)
        self.assertEqual(ipv4.settimeout.call_args_list[0].args, (10,))
        self.assertEqual(ipv6.settimeout.call_args_list[0].args, (3,))
        self.assertEqual(ipv6.settimeout.call_args_list[-1].args, (2,))

    def test_budget_exhaustion_prevents_another_full_timeout(self):
        clock = [0]
        ipv4 = Mock()

        def first_attempt(address):
            clock[0] = 11
            raise socket.timeout()

        ipv4.connect.side_effect = first_attempt
        with patch("knowledge_agent.models.socket.getaddrinfo", return_value=list(self.addresses)), \
                patch("knowledge_agent.models.socket.socket", return_value=ipv4) as factory, \
                patch("knowledge_agent.models.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(socket.timeout):
                _connect_ipv4_first(("example.test", 443), timeout=10)
        self.assertEqual(factory.call_count, 1)
        ipv4.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
