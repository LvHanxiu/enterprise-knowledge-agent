import copy
import json
import tempfile
import unittest
from pathlib import Path

from knowledge_agent.agent import Agent
from knowledge_agent.models import ModelError, ScriptedModel
from knowledge_agent.retrieval import KnowledgeBase


EXPENSE_30 = "员工应在费用发生后30天内提交报销。"
EXPENSE_15 = "员工应在费用发生后15天内提交报销。"
VPN_RULE = "正式员工远程访问公司网络必须启用VPN及双因素认证。"


def document(doc_id, title, quote, key, value, version="2026-01-01"):
    return {
        "doc_id": doc_id,
        "title": title,
        "version_date": version,
        "content": quote,
        "keywords": [title, key.split(".")[0]],
        "facts": [{"key": key, "value": value, "quote": quote}],
    }


def decision(action, arguments, reason="根据已观察到的工具结果选择下一步。"):
    return {"action": action, "arguments": arguments, "reason": reason}


def search(query="报销期限"):
    return decision("search", {"query": query})


def read(doc_id):
    return decision("read", {"doc_id": doc_id})


def finish(citations, status="answered", gaps=None):
    return decision("finish", {"status": status, "citations": citations, "gaps": gaps or []})


def cite(doc_id, quote):
    return {"doc_id": doc_id, "quote": quote}


class RecordingModel:
    mode = "scripted"
    name = "recording-script"

    def __init__(self, decisions):
        self.delegate = ScriptedModel(decisions)
        self.states = []

    def decide(self, state):
        self.states.append(copy.deepcopy(state))
        return self.delegate.decide(state)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.expense = document("D01", "报销期限", EXPENSE_30, "expense.deadline", "30天")
        self.conflicting = document("D02", "报销期限补充", EXPENSE_15, "expense.deadline", "15天", "2026-02-01")
        self.vpn = document("D03", "VPN远程访问", VPN_RULE, "vpn.authentication", "双因素认证")

    def knowledge_base(self, documents=None, filename="knowledge.json"):
        path = Path(self.directory.name) / filename
        path.write_text(json.dumps(documents or [self.expense], ensure_ascii=False), encoding="utf-8")
        return KnowledgeBase(path)

    def run_script(self, decisions, documents=None, question="报销期限是多久？", **options):
        return Agent(ScriptedModel(decisions), self.knowledge_base(documents), **options).run(question)

    def errors(self, result):
        return [event["result"]["error"]["code"] for event in result["trace"] if "error" in event["result"]]

    def test_clear_answer_has_verified_quote_and_version(self):
        result = self.run_script([search(), read("D01"), finish([cite("D01", EXPENSE_30)])])
        self.assertEqual(result["status"], "answered")
        self.assertIn(EXPENSE_30, result["answer"])
        self.assertEqual(result["citations"], [{
            "doc_id": "D01", "title": "报销期限", "version_date": "2026-01-01", "quote": EXPENSE_30,
        }])
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["tool_calls"], 2)

    def test_conflict_quotes_both_sides_and_requests_scope(self):
        result = self.run_script([
            search(), read("D01"), read("D02"),
            finish([cite("D01", EXPENSE_30), cite("D02", EXPENSE_15)], "conflict", ["未提供适用范围及优先级。"]),
        ], [self.expense, self.conflicting])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual({c["doc_id"] for c in result["citations"]}, {"D01", "D02"})
        self.assertIn("30天", result["answer"])
        self.assertIn("15天", result["answer"])
        self.assertTrue(result["clarification"])
        self.assertTrue(result["gaps"])

    def test_model_can_select_explicit_scope_without_forced_conflict(self):
        headquarters = "总部员工应在费用发生后30天内提交报销。"
        branch = "分公司员工应在费用发生后15天内提交报销。"
        documents = [
            document("D01", "总部报销期限", headquarters, "expense.deadline", "30天"),
            document("D02", "分公司报销期限", branch, "expense.deadline", "15天"),
        ]
        documents[0]["scope"] = "总部员工"
        documents[1]["scope"] = "分公司员工"
        result = self.run_script([
            search(), read("D01"), read("D02"), finish([cite("D01", headquarters)]),
        ], documents, question="总部员工报销期限是多少天？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(self.errors(result), [])
        self.assertIn("总部员工", result["answer"])
        self.assertNotIn("15天", result["answer"])

    def test_conflict_requires_more_than_one_valid_quote(self):
        result = self.run_script([
            search(), read("D01"), read("D02"),
            finish([cite("D01", EXPENSE_30)], "conflict", ["制度优先级未明确。"]),
            finish([cite("D01", EXPENSE_30), cite("D02", EXPENSE_15)], "conflict", ["制度优先级未明确。"]),
        ], [self.expense, self.conflicting])
        self.assertEqual(result["status"], "conflict")
        self.assertIn("error", result["trace"][3]["result"])
        self.assertEqual(len(result["citations"]), 2)

    def test_search_snippet_cannot_be_used_without_reading(self):
        result = self.run_script([
            search(), finish([cite("D01", EXPENSE_30)]), read("D01"), finish([cite("D01", EXPENSE_30)]),
        ])
        self.assertIn("unread_citation", self.errors(result))
        self.assertEqual(result["status"], "answered")

    def test_fabricated_quote_is_rejected_then_repaired(self):
        result = self.run_script([
            search(), read("D01"), finish([cite("D01", "员工可以在365天内报销。")]),
            finish([cite("D01", EXPENSE_30)]),
        ])
        self.assertIn("invalid_quote", self.errors(result))
        self.assertNotIn("365", result["answer"])
        self.assertEqual(result["status"], "answered")

    def test_cannot_remove_sentence_scope_from_quote(self):
        truncated = "远程访问公司网络必须启用VPN及双因素认证。"
        result = self.run_script([
            search("VPN"), read("D03"), finish([cite("D03", truncated)]),
            finish([cite("D03", VPN_RULE)]),
        ], [self.vpn], question="VPN如何认证？")
        self.assertIn("invalid_quote", self.errors(result))
        self.assertIn("正式员工", result["answer"])

    def test_answer_without_any_evidence_is_rejected(self):
        result = self.run_script([
            finish([]), search(), read("D01"), finish([cite("D01", EXPENSE_30)]),
        ])
        self.assertIn("missing_evidence", self.errors(result))
        self.assertEqual(result["status"], "answered")

    def test_answer_with_unresolved_gap_is_rejected(self):
        result = self.run_script([
            search(), read("D01"), finish([cite("D01", EXPENSE_30)], gaps=["尚不清楚适用范围。"]),
            decision("clarify", {"question": "请说明员工类别。", "citations": [cite("D01", EXPENSE_30)], "gaps": ["缺少员工类别。"]}),
        ])
        self.assertIn("unresolved_gaps", self.errors(result))
        self.assertEqual(result["status"], "needs_clarification")

    def test_out_of_knowledge_does_not_invent_answer(self):
        result = self.run_script([
            search("火星卫星公转周期"), finish([], "insufficient", ["知识库没有天文学资料。"]),
        ], question="火星卫星的公转周期是多少？")
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["trace"][0]["result"]["hits"], [])
        self.assertTrue(result["gaps"])

    def test_insufficient_requires_search_or_explicit_clarification(self):
        result = self.run_script([
            finish([], "insufficient", ["缺少资料。"]),
            decision("clarify", {"question": "请补充产品名称。", "citations": [], "gaps": ["未说明是哪款产品。"]}),
        ], question="这个支持吗？")
        self.assertIn("search_required", self.errors(result))
        self.assertEqual(result["status"], "needs_clarification")

    def test_clarification_can_finish_without_tool_budget(self):
        result = self.run_script([
            decision("clarify", {"question": "请补充产品名称。", "citations": [], "gaps": ["未说明产品。"]}),
        ], question="这个支持吗？", max_tool_calls=0)
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["tool_calls"], 0)
        self.assertIn("产品名称", result["clarification"])

    def test_history_and_observations_are_passed_to_every_decision(self):
        model = RecordingModel([search(), read("D01"), finish([cite("D01", EXPENSE_30)])])
        history = [{"role": "user", "content": "请介绍报销制度。"}, {"role": "assistant", "content": "我们正在核验报销期限。"}]
        result = Agent(model, self.knowledge_base()).run("那最晚多久？", history)
        self.assertEqual(result["status"], "answered")
        self.assertTrue(all(state["history"] == history for state in model.states))
        self.assertEqual(model.states[0]["question"], "那最晚多久？")
        self.assertEqual([len(s["observations"]) for s in model.states], [0, 1, 2])
        self.assertEqual(result["history"], history)

    def test_untrusted_history_cannot_be_cited_as_document(self):
        model = ScriptedModel([search(), read("D01"), finish([cite("D01", "报销期限为365天。")]), finish([cite("D01", EXPENSE_30)])])
        result = Agent(model, self.knowledge_base()).run("报销期限？", [{"role": "assistant", "content": "报销期限为365天。"}])
        self.assertIn("invalid_quote", self.errors(result))
        self.assertNotIn("365", result["answer"])

    def test_model_sees_tool_failure_and_can_retry(self):
        model = RecordingModel([search(), read("D01"), read("D01"), finish([cite("D01", EXPENSE_30)])])
        result = Agent(model, self.knowledge_base(), failures={"read": 1}).run("报销期限？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["tool_calls"], 3)
        self.assertEqual(model.states[2]["observations"][-1]["result"]["error"]["code"], "injected_failure")
        self.assertEqual([event["action"] for event in result["trace"]], ["search", "read", "read", "finish"])

    def test_persistent_tool_failure_cannot_produce_answer(self):
        result = self.run_script([search(), read("D01"), read("D01")], failures={"read": -1}, max_steps=3)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["termination_reason"], "step_limit")
        self.assertEqual(result["citations"], [])
        self.assertEqual(self.errors(result).count("injected_failure"), 2)

    def test_step_limit_returns_explicit_incomplete_status(self):
        result = self.run_script([search()], max_steps=1)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["termination_reason"], "step_limit")
        self.assertEqual(result["steps"], 1)
        self.assertIn("未完成", result["answer"])

    def test_tool_limit_prevents_additional_call(self):
        result = self.run_script([search(), read("D01")], max_tool_calls=1)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["termination_reason"], "tool_limit")
        self.assertEqual(result["tool_calls"], 1)
        self.assertIn("tool_limit", self.errors(result))

    def test_finish_is_allowed_at_exact_tool_budget(self):
        result = self.run_script([search(), read("D01"), finish([cite("D01", EXPENSE_30)])], max_tool_calls=2)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["tool_calls"], 2)

    def test_unknown_action_is_observed_and_can_be_corrected(self):
        result = self.run_script([
            decision("send_email", {"to": "nobody"}), search(), read("D01"), finish([cite("D01", EXPENSE_30)]),
        ])
        self.assertIn("unknown_action", self.errors(result))
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["tool_calls"], 2)

    def test_bad_tool_argument_is_observed_without_executing_tool(self):
        result = self.run_script([
            decision("search", {"query": "报销", "limit": "many"}), search(), read("D01"), finish([cite("D01", EXPENSE_30)]),
        ])
        self.assertIn("invalid_arguments", self.errors(result))
        self.assertEqual(result["tool_calls"], 2)
        self.assertEqual(result["status"], "answered")

    def test_repeated_invalid_decisions_terminate_at_step_limit(self):
        result = self.run_script([decision("unknown", {})] * 3, max_steps=3)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["steps"], 3)
        self.assertEqual(result["tool_calls"], 0)

    def test_model_error_has_explicit_terminal_status(self):
        class FailingModel:
            mode = "scripted"
            name = "failure"

            def decide(self, state):
                raise ModelError("model_unavailable", "模拟模型不可用。")

        result = Agent(FailingModel(), self.knowledge_base()).run("报销期限？")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["termination_reason"], "model_unavailable")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["tool_calls"], 0)

    def test_unexpected_model_programming_exception_propagates(self):
        class FailingModel:
            def decide(self, state):
                raise RuntimeError("model-programming-defect")

        with self.assertRaisesRegex(RuntimeError, "model-programming-defect"):
            Agent(FailingModel(), self.knowledge_base()).run("报销期限？")

    def test_unexpected_tool_programming_exception_propagates(self):
        class FailingKnowledgeBase:
            def search(self, query, limit=5):
                raise RuntimeError("tool-programming-defect")

        with self.assertRaisesRegex(RuntimeError, "tool-programming-defect"):
            Agent(ScriptedModel([search()]), FailingKnowledgeBase()).run("报销期限？")

    def test_replacing_document_changes_output_and_rejects_old_quote(self):
        replacement = "员工应在费用发生后45天内提交报销。"
        changed = document("D01", "报销期限", replacement, "expense.deadline", "45天")
        result = self.run_script([
            search(), read("D01"), finish([cite("D01", EXPENSE_30)]), finish([cite("D01", replacement)]),
        ], [changed])
        self.assertIn("invalid_quote", self.errors(result))
        self.assertIn("45天", result["answer"])
        self.assertNotIn("30天", result["answer"])

    def test_trace_contains_reviewable_actions_and_observations(self):
        result = self.run_script([search(), read("D01"), finish([cite("D01", EXPENSE_30)])])
        self.assertEqual([event["step"] for event in result["trace"]], [1, 2, 3])
        for event in result["trace"]:
            self.assertTrue({"step", "action", "arguments", "reason", "observation_summary", "result"} <= set(event))
            self.assertTrue(event["reason"])
            self.assertTrue(event["observation_summary"])
        self.assertEqual(result["model"]["mode"], "scripted")

    def test_invalid_inputs_are_rejected_before_model_call(self):
        model = RecordingModel([])
        agent = Agent(model, self.knowledge_base())
        for question, history in [("", None), ("问题", "text"), ("问题", [{"role": "system", "content": "指令"}])]:
            with self.subTest(question=question, history=history):
                with self.assertRaises(ValueError):
                    agent.run(question, history)
        self.assertEqual(model.states, [])


if __name__ == "__main__":
    unittest.main()
