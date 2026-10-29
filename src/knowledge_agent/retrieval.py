import copy
import json
import math
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path


class ToolError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


ALIASES = (
    ("报销", "报账", "报帐", "费用报支", "垫付", "reimbursement", "expense", "expenses"),
    ("vpn", "虚拟专网", "远程办公", "远程连接", "内网连接", "内网访问"),
    ("年假", "年休假", "带薪休假", "annual leave", "paid leave"),
    ("发票", "票据", "小票", "invoice", "receipt"),
    ("协作云", "collabcloud", "协作平台"),
    ("导出", "下载表格", "下载数据", "export", "csv"),
    ("密码", "口令", "登录凭证", "password", "reset password"),
)
STOP_WORDS = {
    "请问", "你好", "公司", "员工", "企业", "如何", "怎么", "什么", "多少", "多久",
    "是否", "可以", "需要", "规定", "制度", "政策", "问题", "一下", "哪些", "多少天",
    "请", "的", "了", "吗", "呢", "是", "有", "我", "在", "a", "an", "the", "is",
    "are", "do", "does", "what", "how", "when", "can", "i", "my", "for", "to", "of",
    "and", "in", "it", "policy", "company", "employee", "employees", "please",
}


def _normalize(text):
    return unicodedata.normalize("NFKC", text).casefold()


def _contains(text, phrase):
    if re.search(r"[a-z]", phrase):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text))
    return phrase in text


def _concepts(text):
    text = _normalize(text)
    return {index for index, terms in enumerate(ALIASES) if any(_contains(text, term) for term in terms)}


def _tokens(text):
    text = _normalize(text)
    for word in sorted((word for word in STOP_WORDS if re.search(r"[\u4e00-\u9fff]", word)), key=len, reverse=True):
        text = text.replace(word, " ")
    tokens = []
    for word in re.findall(r"[a-z][a-z0-9._-]*|[\u4e00-\u9fff]+", text):
        if word in STOP_WORDS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            tokens.extend(word[index:index + 2] for index in range(len(word) - 1))
        else:
            tokens.append(word)
    return tokens


class KnowledgeBase:
    def __init__(self, path):
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ToolError("invalid_corpus", "知识库必须是可读取的 UTF-8 JSON 文件。") from error
        if not isinstance(raw, list):
            raise ToolError("invalid_corpus", "知识库顶层必须是文档数组。")
        self._documents = {}
        for document in raw:
            document = self._normalize_document(document)
            doc_id = document["doc_id"]
            if doc_id in self._documents:
                raise ToolError("invalid_corpus", "知识库包含重复文档 ID。")
            self._documents[doc_id] = document
        self._frequencies = {}
        self._concept_index = {}
        document_frequency = Counter()
        for doc_id, document in self._documents.items():
            text = " ".join([document["title"], document["content"], *document["keywords"]])
            counts = Counter(_tokens(text))
            self._frequencies[doc_id] = counts
            self._concept_index[doc_id] = _concepts(text)
            document_frequency.update(counts.keys())
        self._idf = {
            token: math.log(1 + (len(raw) - count + 0.5) / (count + 0.5))
            for token, count in document_frequency.items()
        }
        self._average_length = sum(sum(counts.values()) for counts in self._frequencies.values()) / max(len(raw), 1)

    @property
    def documents(self):
        return copy.deepcopy(self._documents)

    @staticmethod
    def _normalize_document(document):
        if not isinstance(document, dict):
            raise ToolError("invalid_corpus", "每个文档必须是对象。")
        for field in ("doc_id", "version_date", "content"):
            if not isinstance(document.get(field), str) or not document[field].strip():
                raise ToolError("invalid_corpus", f"文档的 {field} 必须是非空字符串。")
        try:
            if date.fromisoformat(document["version_date"]).isoformat() != document["version_date"]:
                raise ValueError
        except ValueError as error:
            raise ToolError("invalid_corpus", "version_date 必须是有效的 YYYY-MM-DD 日期。") from error
        document = copy.deepcopy(document)
        document.setdefault("title", document["doc_id"])
        if not isinstance(document["title"], str) or not document["title"].strip():
            raise ToolError("invalid_corpus", "title 必须是非空字符串。")
        document.setdefault("keywords", [])
        keywords = document["keywords"]
        if not isinstance(keywords, list) or not all(isinstance(word, str) and word.strip() for word in keywords):
            raise ToolError("invalid_corpus", "keywords 必须是非空字符串组成的数组。")
        if "scope" in document and not isinstance(document["scope"], str):
            raise ToolError("invalid_corpus", "scope 必须是字符串。")
        facts = document.get("facts", [])
        warnings = []
        if not isinstance(facts, list):
            warnings.append("facts 不是数组，已忽略全部事实提示；请以原文为准。")
            facts = []
        valid_facts = []
        for index, fact in enumerate(facts, 1):
            if not isinstance(fact, dict) or any(not isinstance(fact.get(key), str) or not fact[key].strip() for key in ("key", "value", "quote")):
                warnings.append(f"已忽略第 {index} 条事实提示：key、value、quote 必须是非空字符串。")
            elif fact["quote"] not in document["content"] or fact["value"] not in fact["quote"]:
                warnings.append(f"已忽略第 {index} 条事实提示：引用或值与当前原文不一致，请以原文为准。")
            else:
                valid_facts.append(fact)
        document["facts"] = valid_facts
        document["index_warnings"] = warnings
        return document

    def search(self, query, limit=5):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ToolError("invalid_arguments", "query 必须是 1 至 500 字符的非空字符串。")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ToolError("invalid_arguments", "limit 必须是 1 至 20 之间的整数。")
        query = query.strip()
        normalized = _normalize(query)
        terms = set(_tokens(query))
        concepts = _concepts(query)
        matches = []
        for doc_id, document in self._documents.items():
            counts = self._frequencies[doc_id]
            overlap = terms.intersection(counts)
            shared_concepts = concepts.intersection(self._concept_index[doc_id])
            direct_keywords = [word for word in document["keywords"] if _contains(normalized, _normalize(word))]
            if not overlap and not shared_concepts and not direct_keywords:
                continue
            length_ratio = sum(counts.values()) / max(self._average_length, 1)
            lexical_score = sum(
                self._idf[token] * counts[token] * 2.2 / (counts[token] + 1.2 * (0.25 + 0.75 * length_ratio))
                for token in overlap
            )
            if not shared_concepts and not direct_keywords and len(overlap) < 2:
                continue
            score = lexical_score + 4 * len(shared_concepts) + 2 * min(len(direct_keywords), 3)
            matches.append({
                "doc_id": doc_id,
                "title": document["title"],
                "version_date": document["version_date"],
                "snippet": self._snippet(document["content"], terms, concepts),
                "score": round(score, 4),
                "fact_keys": list(dict.fromkeys(fact["key"] for fact in document["facts"])),
                "index_warnings": list(document["index_warnings"]),
            })
        matches.sort(key=lambda hit: (-hit["score"], hit["doc_id"]))
        return {"query": query, "hits": matches[:limit], "total": len(matches), "truncated": len(matches) > limit}

    @staticmethod
    def _snippet(content, terms, concepts):
        sentences = [sentence.strip() for sentence in re.split(r"(?<=[。！？.!?])\s*|\n+", content) if sentence.strip()]
        selected = max(sentences, key=lambda sentence: len(terms.intersection(_tokens(sentence))) + 2 * len(concepts.intersection(_concepts(sentence))))
        return selected if len(selected) <= 160 else selected[:157] + "..."

    def read(self, doc_id):
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise ToolError("invalid_arguments", "doc_id 必须是非空字符串。")
        if doc_id not in self._documents:
            raise ToolError("not_found", "未找到指定文档。")
        return copy.deepcopy(self._documents[doc_id])
