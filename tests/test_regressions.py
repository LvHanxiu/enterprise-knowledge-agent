import json
import tempfile
import unittest
from pathlib import Path

from knowledge_agent.agent import Agent
from knowledge_agent.cli import check_expected
from knowledge_agent.models import ScriptedModel
from knowledge_agent.retrieval import KnowledgeBase


ROOT = Path(__file__).resolve().parents[1]


def action(name, arguments):
    return {"action": name, "arguments": arguments, "reason": "根据本次工具观测核验原文。"}


def finish(citations, status="answered", gaps=None):
    return action("finish", {"status": status, "citations": citations, "gaps": gaps or []})


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = KnowledgeBase(ROOT / "data" / "knowledge_base.json")

    def fixture(self, documents):
        path = Path(self.directory.name) / "regression.json"
        path.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
        return KnowledgeBase(path)

    def errors(self, result):
        return [event["result"]["error"]["code"] for event in result["trace"] if "error" in event["result"]]

    def test_semicolon_cannot_remove_scope_from_a_quote(self):
        for separator in ("；", ";"):
            with self.subTest(separator=separator):
                documents = [self.base.read("D01"), self.base.read("D02")]
                for document in documents:
                    quote = document["content"].split("。")[0] + separator + "适用员工范围尚未明确。"
                    document["content"] = quote
                    document["facts"] = []
                truncated = documents[0]["content"].split(separator)[0] + separator
                complete = [{"doc_id": doc["doc_id"], "quote": doc["content"]} for doc in documents]
                model = ScriptedModel([
                    action("search", {"query": "报销期限"}),
                    action("read", {"doc_id": "D01"}),
                    action("read", {"doc_id": "D02"}),
                    finish([{"doc_id": "D01", "quote": truncated}]),
                    finish(complete, "conflict", ["适用员工范围及制度优先级尚未明确。"]),
                ])
                result = Agent(model, self.fixture(documents)).run("报销期限是多少天？")
                self.assertEqual(result["status"], "conflict")
                self.assertIn("error", result["trace"][3]["result"])
                self.assertEqual({c["doc_id"] for c in result["citations"]}, {"D01", "D02"})
                self.assertTrue(all("适用员工范围尚未明确" in c["quote"] for c in result["citations"]))

    def test_narrow_search_can_complete_after_an_earlier_broad_search(self):
        documents = []
        for index in range(1, 12):
            documents.append({
                "doc_id": f"D{index:02}", "title": "知识手册", "version_date": "2026-01-01",
                "content": "VPN必须启用双因素认证。" if index == 1 else f"行政资料第{index}项按登记流程办理。",
                "keywords": ["手册", "VPN"] if index == 1 else ["手册"], "facts": [],
            })
        base = self.fixture(documents)
        self.assertEqual(base.search("手册")["total"], 11)
        self.assertEqual(base.search("VPN")["total"], 1)
        model = ScriptedModel([
            action("search", {"query": "手册", "limit": 5}),
            action("search", {"query": "VPN", "limit": 5}),
            action("read", {"doc_id": "D01"}),
            finish([{"doc_id": "D01", "quote": documents[0]["content"]}]),
        ])
        result = Agent(model, base).run("VPN如何认证？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(self.errors(result), [])
        self.assertEqual(result["tool_calls"], 3)
        self.assertIn("双因素认证", result["answer"])

    def test_complete_unindexed_sentence_can_answer_the_question(self):
        document = self.base.read("D01")
        quote = "报销申请通过OA系统提交。"
        document["content"] += quote
        model = ScriptedModel([
            action("search", {"query": "报销申请"}),
            action("read", {"doc_id": "D01"}),
            finish([{"doc_id": "D01", "quote": quote}]),
        ])
        result = Agent(model, self.fixture([document])).run("报销申请在哪里提交？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(self.errors(result), [])
        self.assertIn(quote, result["answer"])

    def test_raw_documents_can_report_conflict_without_facts(self):
        for facts_mode in ("empty", "missing"):
            with self.subTest(facts_mode=facts_mode):
                documents = [self.base.read("D01"), self.base.read("D02")]
                for document in documents:
                    if facts_mode == "empty":
                        document["facts"] = []
                    else:
                        document.pop("facts", None)
                citations = [{"doc_id": doc["doc_id"], "quote": doc["content"]} for doc in documents]
                model = ScriptedModel([
                    action("search", {"query": "报销期限"}),
                    action("read", {"doc_id": "D01"}),
                    action("read", {"doc_id": "D02"}),
                    finish(citations, "conflict", ["两份原文给出的报销期限不同，适用规则未说明。"]),
                ])
                result = Agent(model, self.fixture(documents)).run("报销期限是多少天？")
                self.assertEqual(result["status"], "conflict")
                self.assertEqual(self.errors(result), [])
                self.assertEqual({c["doc_id"] for c in result["citations"]}, {"D01", "D02"})

    def test_different_quotes_in_one_document_can_describe_conflict(self):
        first = "员工费用报销应在费用发生之日起30天内提交申请。"
        second = "员工费用报销必须在费用发生之日起15天内提交申请。"
        document = self.base.read("D01")
        document["content"] = first + second
        document.pop("facts", None)
        model = ScriptedModel([
            action("search", {"query": "报销期限"}),
            action("read", {"doc_id": "D01"}),
            finish([{"doc_id": "D01", "quote": first}, {"doc_id": "D01", "quote": second}],
                   "conflict", ["同一文档出现两种期限，未说明适用条件。"]),
        ])
        result = Agent(model, self.fixture([document])).run("报销期限是多少天？")
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(len(result["citations"]), 2)
        self.assertEqual(self.errors(result), [])

    def test_changed_body_is_authoritative_over_legacy_facts(self):
        document = self.base.read("D01")
        old_quote = "员工费用报销应在费用发生之日起 30 天内提交申请。"
        replacement = "员工费用报销应在费用发生之日起 45 天内提交申请。"
        document["content"] = replacement
        document["facts"] = [{"key": "expense.deadline", "value": "30 天", "quote": old_quote}]
        model = ScriptedModel([
            action("search", {"query": "报销期限"}),
            action("read", {"doc_id": "D01"}),
            finish([{"doc_id": "D01", "quote": replacement}]),
        ])
        result = Agent(model, self.fixture([document])).run("报销期限是多少天？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(self.errors(result), [])
        self.assertIn("45 天", result["answer"])
        self.assertNotIn("30 天", result["answer"])

    def test_unhashable_history_roles_are_validation_errors(self):
        for role in ([], {}):
            with self.subTest(role=role):
                with self.assertRaises(ValueError):
                    Agent(ScriptedModel([]), self.base).run("问题", [{"role": role, "content": "历史"}])

    def test_missing_scope_acceptance_rejects_wrong_numeric_answer(self):
        cases = json.loads((ROOT / "examples" / "cases.json").read_text(encoding="utf-8"))
        expected = next(case["expected"] for case in cases if case["id"] == "missing_scope")
        self.assertIn("5000", expected.get("answer_excludes", []))
        bad_result = {
            "status": "insufficient", "answer": "现有资料不足以回答。5000 条。", "citations": [],
            "gaps": ["企业版导出上限未提供。"],
            "steps": 2, "tool_calls": 2, "limits": {"max_steps": 12, "max_tool_calls": 8},
            "trace": [{"action": "search", "result": {}}, {"action": "read", "result": {}}],
        }
        checks = check_expected(bad_result, expected)
        self.assertFalse(all(checks.values()))
        self.assertFalse(checks["answer_excludes:5000"])
        self.assertTrue(all(value for key, value in checks.items() if key != "answer_excludes:5000"))


if __name__ == "__main__":
    unittest.main()
