import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_agent.retrieval import KnowledgeBase, ToolError


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.kb = KnowledgeBase(ROOT / "data" / "knowledge_base.json")

    def temporary_corpus(self, documents):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "corpus.json"
        path.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
        return path

    def test_conflicting_policies_are_both_retrieved(self):
        result = self.kb.search("报销期限多久？")
        self.assertEqual({hit["doc_id"] for hit in result["hits"][:2]}, {"D01", "D02"})
        self.assertTrue(all("expense.deadline" in hit["fact_keys"] for hit in result["hits"][:2]))

    def test_reformulation_and_english(self):
        for query in ("垫付费用后几天以内要报账？", "reimbursement deadline", "expense submission"):
            with self.subTest(query=query):
                ids = {hit["doc_id"] for hit in self.kb.search(query)["hits"]}
                self.assertTrue({"D01", "D02"}.issubset(ids))

    def test_vpn_and_annual_leave(self):
        for query, doc_id in (("远程办公如何连接内网", "D03"), ("VPN setup", "D03"), ("annual leave", "D04"), ("年休假提前几天申请", "D04")):
            with self.subTest(query=query):
                self.assertEqual(self.kb.search(query)["hits"][0]["doc_id"], doc_id)

    def test_product_question(self):
        self.assertEqual(self.kb.search("CollabCloud export limit")["hits"][0]["doc_id"], "D06")
        self.assertEqual(self.kb.search("协作云标准版一次最多导出多少条任务？")["hits"][0]["doc_id"], "D06")

    def test_out_of_scope_queries_are_empty(self):
        for query in ("明天上海天气如何", "火星的卫星有几颗", "公司股票明年涨多少", "quantum entanglement experiment"):
            with self.subTest(query=query):
                self.assertEqual(self.kb.search(query)["hits"], [])

    def test_search_limit_and_total(self):
        result = self.kb.search("报销", limit=1)
        self.assertEqual(len(result["hits"]), 1)
        self.assertGreaterEqual(result["total"], 2)
        self.assertTrue(result["truncated"])
        self.assertFalse(self.kb.search("报销", limit=20)["truncated"])

    def test_read_returns_original_with_version(self):
        document = self.kb.read("D01")
        self.assertEqual(document["version_date"], "2026-01-10")
        self.assertIn("30 天", document["content"])
        document["content"] = "altered"
        self.assertIn("30 天", self.kb.read("D01")["content"])

    def test_public_documents_cannot_mutate_corpus(self):
        self.kb.documents["D01"]["facts"][0]["value"] = "999 天"
        self.assertEqual(self.kb.read("D01")["facts"][0]["value"], "30 天")

    def test_unknown_document(self):
        with self.assertRaises(ToolError) as caught:
            self.kb.read("NO_SUCH_DOCUMENT")
        self.assertEqual(caught.exception.code, "not_found")
        self.assertFalse(caught.exception.retryable)

    def test_invalid_search_arguments(self):
        for query, limit in (("", 5), (None, 5), ("x" * 501, 5), ("报销", 0), ("报销", 21), ("报销", True), ("报销", "2")):
            with self.subTest(query=query, limit=limit), self.assertRaises(ToolError):
                self.kb.search(query, limit)

    def test_empty_corpus(self):
        self.assertEqual(KnowledgeBase(self.temporary_corpus([])).search("VPN")["hits"], [])

    def test_duplicate_document_id_rejected(self):
        document = self.kb.read("D01")
        with self.assertRaises(ToolError):
            KnowledgeBase(self.temporary_corpus([document, document]))

    def test_invalid_metadata_rejected(self):
        changes = ({"version_date": "2026-02-30"}, {"version_date": "20260101"}, {"doc_id": ""}, {"title": ""}, {"keywords": "VPN"}, {"content": None})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ToolError):
                document = self.kb.read("D01")
                document.update(change)
                KnowledgeBase(self.temporary_corpus([document]))

    def test_invalid_fact_hints_are_ignored_with_warning(self):
        for field, value in (("quote", "原文没有这个内容。"), ("value", "999 天")):
            with self.subTest(field=field):
                document = self.kb.read("D01")
                document["facts"][0][field] = value
                loaded = KnowledgeBase(self.temporary_corpus([document])).read("D01")
                self.assertEqual(loaded["facts"], [])
                self.assertEqual(loaded["content"], document["content"])
                self.assertIn("已忽略", loaded["index_warnings"][0])

    def test_fact_keys_need_not_be_unique(self):
        document = self.kb.read("D01")
        quote = "分公司员工应在费用发生之日起 15 天内提交报销。"
        document["content"] += quote
        document["facts"].append({"key": "expense.deadline", "value": "15 天", "quote": quote})
        loaded = KnowledgeBase(self.temporary_corpus([document])).read("D01")
        self.assertEqual(len(loaded["facts"]), 2)
        self.assertEqual(loaded["index_warnings"], [])

    def test_snippets_come_from_documents(self):
        for hit in self.kb.search("VPN 年假 报销 导出 密码", limit=20)["hits"]:
            self.assertIn(hit["snippet"], self.kb.read(hit["doc_id"])["content"])

    def test_changed_corpus_is_reindexed(self):
        original = KnowledgeBase(ROOT / "data" / "single_policy.json")
        changed = KnowledgeBase(ROOT / "data" / "changed_policy.json")
        self.assertIn("30 天", original.search("报销期限")["hits"][0]["snippet"])
        self.assertIn("45 天", changed.search("报销期限")["hits"][0]["snippet"])
        self.assertEqual(changed.read("D01")["facts"][0]["value"], "45 天")

    def test_documents_need_only_id_date_and_content(self):
        document = {"doc_id": "D08", "version_date": "2026-10-28", "content": "VPN 客户端支持 Windows 和 macOS。"}
        base = KnowledgeBase(self.temporary_corpus([document]))
        loaded = base.read("D08")
        self.assertEqual(loaded["title"], "D08")
        self.assertEqual(loaded["facts"], [])
        self.assertEqual(loaded["keywords"], [])
        self.assertEqual(loaded["index_warnings"], [])
        self.assertEqual(base.search("VPN")["hits"][0]["doc_id"], "D08")

    def test_unicode_document_id_is_an_opaque_identifier(self):
        document = {"doc_id": "财务/报销制度 第一版", "version_date": "2026-10-28", "content": "报销期限为 30 天。"}
        base = KnowledgeBase(self.temporary_corpus([document]))
        self.assertEqual(base.read(document["doc_id"])["content"], document["content"])
        self.assertEqual(base.search("报销")["hits"][0]["doc_id"], document["doc_id"])

    def test_content_only_change_discards_stale_hints_and_reindexes(self):
        document = self.kb.read("D01")
        document["content"] = document["content"].replace("30 天", "45 天")
        base = KnowledgeBase(self.temporary_corpus([document]))
        loaded = base.read("D01")
        hit = base.search("报销期限")["hits"][0]
        self.assertEqual(loaded["facts"], [])
        self.assertIn("45 天", loaded["content"])
        self.assertIn("45 天", hit["snippet"])
        self.assertEqual(hit["fact_keys"], [])
        self.assertTrue(hit["index_warnings"])
        self.assertNotIn("30 天", json.dumps(loaded, ensure_ascii=False))

    def test_new_content_does_not_need_a_fact_hint(self):
        document = self.kb.read("D03")
        additional = "企业 VPN 支持 Windows 和 macOS 客户端。"
        document["content"] += additional
        base = KnowledgeBase(self.temporary_corpus([document]))
        loaded = base.read("D03")
        self.assertIn(additional, loaded["content"])
        self.assertEqual(len(loaded["facts"]), 2)
        self.assertEqual(loaded["index_warnings"], [])
        self.assertEqual(base.search("Windows macOS")["hits"][0]["snippet"], additional)

    def test_malformed_optional_facts_do_not_block_original_text(self):
        for facts in ("bad metadata", [None, {"key": "incomplete"}]):
            with self.subTest(facts=facts):
                document = self.kb.read("D01")
                document["facts"] = facts
                loaded = KnowledgeBase(self.temporary_corpus([document])).read("D01")
                self.assertEqual(loaded["facts"], [])
                self.assertTrue(loaded["index_warnings"])
                self.assertEqual(loaded["content"], document["content"])

    def test_malformed_json_and_wrong_top_level_fail_clearly(self):
        path = self.temporary_corpus({"documents": []})
        with self.assertRaises(ToolError) as caught:
            KnowledgeBase(path)
        self.assertEqual(caught.exception.code, "invalid_corpus")
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ToolError) as caught:
            KnowledgeBase(path)
        self.assertEqual(caught.exception.code, "invalid_corpus")

    def test_missing_required_field_is_rejected(self):
        for field in ("doc_id", "version_date", "content"):
            with self.subTest(field=field), self.assertRaises(ToolError):
                document = self.kb.read("D01")
                del document[field]
                KnowledgeBase(self.temporary_corpus([document]))

    def test_content_larger_than_old_arbitrary_limit_can_be_read(self):
        content = "VPN 说明。" * 8000
        document = {"doc_id": "large", "version_date": "2026-10-28", "content": content}
        self.assertGreater(len(content), 50000)
        self.assertEqual(KnowledgeBase(self.temporary_corpus([document])).read("large")["content"], content)


if __name__ == "__main__":
    unittest.main()
