import copy
import re
import time
from datetime import datetime, timezone

from .models import ModelError
from .retrieval import ToolError


class DecisionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _text(value, name, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DecisionError("invalid_arguments", f"{name} 必须是 1..{maximum} 字符的非空字符串。")
    return value.strip()


def _whole_quote(content, quote):
    starts, ends = set(), set()
    previous = 0
    boundaries = [(match.start(), match.end()) for match in re.finditer(r"(?<=[。！？!?])\s*|(?<=\.)\s+|\n+", content)]
    for left, right in boundaries + [(len(content), len(content))]:
        piece = content[previous:left]
        if piece.strip():
            starts.add(previous + len(piece) - len(piece.lstrip()))
            ends.add(left - len(piece) + len(piece.rstrip()))
        previous = right
    return any(match.start() in starts and match.end() in ends for match in re.finditer(re.escape(quote), content))


class Agent:
    def __init__(self, model, knowledge_base, max_steps=12, max_tool_calls=8, failures=None):
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps 必须是正整数。")
        if type(max_tool_calls) is not int or max_tool_calls < 0:
            raise ValueError("max_tool_calls 必须是非负整数。")
        failures = {} if failures is None else failures
        if not isinstance(failures, dict) or any(
            key not in {"search", "read"} or type(value) is not int or value < -1
            for key, value in failures.items()
        ):
            raise ValueError("failures 仅接受 search/read 的失败次数，-1 表示持续失败。")
        self.model = model
        self.knowledge_base = knowledge_base
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.failures = dict(failures)

    def run(self, question, history=None):
        try:
            question = _text(question, "question", 4000)
        except DecisionError as exc:
            raise ValueError(str(exc)) from exc
        history = [] if history is None else history
        if not isinstance(history, list) or len(history) > 20:
            raise ValueError("history 必须是至多 20 条消息的数组。")
        for item in history:
            if (not isinstance(item, dict) or set(item) != {"role", "content"}
                    or not isinstance(item["role"], str) or item["role"] not in {"user", "assistant"}
                    or not isinstance(item["content"], str)
                    or not item["content"].strip() or len(item["content"]) > 4000):
                raise ValueError("history 消息必须包含 user/assistant role 和非空 content。")
        started = time.monotonic()
        self._trace = []
        self._read = {}
        self._tool_calls = 0
        self._failures = dict(self.failures)
        self._question = question
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._usage = {}
        self._model_responses = []
        self._history = copy.deepcopy(history)
        for step in range(1, self.max_steps + 1):
            state = {
                "question": question,
                "history": copy.deepcopy(history),
                "observations": copy.deepcopy(self._trace),
                "limits": {
                    "max_steps": self.max_steps,
                    "max_tool_calls": self.max_tool_calls,
                    "remaining_steps": self.max_steps - step + 1,
                    "remaining_tool_calls": self.max_tool_calls - self._tool_calls,
                },
            }
            try:
                decision = self.model.decide(state)
                metadata = getattr(self.model, "last_metadata", {})
                if metadata:
                    self._model_responses.append({"step": step, **{
                        key: value for key, value in metadata.items()
                        if key in {"provider", "model", "response_id"} and isinstance(value, str)
                    }})
                for key, value in getattr(self.model, "last_usage", {}).items():
                    if type(value) is int and value >= 0:
                        self._usage[key] = self._usage.get(key, 0) + value
            except ModelError as exc:
                self._record(step, "model_error", {}, "模型请求未成功。", {
                    "error": {"code": exc.code, "message": str(exc), "retryable": False}
                })
                return self._result("error", [], ["模型请求失败，未完成资料核验。"], "", exc.code, started)
            try:
                action, args, reason = self._validate_decision(decision)
            except DecisionError as exc:
                self._record(step, "invalid_decision", {}, "模型动作未通过协议校验。", {
                    "error": {"code": exc.code, "message": str(exc), "retryable": True}
                })
                continue
            if action in {"search", "read"}:
                if self._tool_calls >= self.max_tool_calls:
                    self._record(step, action, args, reason, {
                        "error": {"code": "tool_limit", "message": "工具调用次数已达上限。", "retryable": False}
                    })
                    return self._result("incomplete", [], ["工具调用次数达到上限，证据未充分核验。"], "", "tool_limit", started)
                self._tool_calls += 1
                try:
                    self._maybe_fail(action)
                    if action == "search":
                        observed = self.knowledge_base.search(**args)
                    else:
                        observed = self.knowledge_base.read(**args)
                        self._read[observed["doc_id"]] = observed
                except ToolError as exc:
                    observed = {"error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable}}
                self._record(step, action, args, reason, observed)
                continue
            try:
                status, citations, gaps, clarification = self._validate_completion(action, args)
            except DecisionError as exc:
                self._record(step, action, args, reason, {
                    "error": {"code": exc.code, "message": str(exc), "retryable": True}
                })
                continue
            self._record(step, action, args, reason, {"accepted": True, "status": status})
            return self._result(status, citations, gaps, clarification, "completed", started)
        return self._result("incomplete", [], ["模型执行步数达到上限，任务尚未完成。"], "", "step_limit", started)

    def _validate_decision(self, decision):
        if not isinstance(decision, dict) or set(decision) != {"action", "arguments", "reason"}:
            raise DecisionError("invalid_decision", "动作必须包含且仅包含 action、arguments、reason。")
        action = decision["action"]
        if not isinstance(action, str) or action not in {"search", "read", "finish", "clarify"}:
            raise DecisionError("unknown_action", "可用动作：search、read、finish、clarify。")
        args = decision["arguments"]
        if not isinstance(args, dict):
            raise DecisionError("invalid_arguments", "arguments 必须是 JSON 对象。")
        reason = _text(decision["reason"], "reason", 300)
        if action == "search":
            if not {"query"} <= set(args) <= {"query", "limit"}:
                raise DecisionError("invalid_arguments", "search 仅接受 query 和可选 limit。")
            args = dict(args)
            args["query"] = _text(args["query"], "query", 500)
            if "limit" in args and (type(args["limit"]) is not int or not 1 <= args["limit"] <= 10):
                raise DecisionError("invalid_arguments", "limit 必须为 1..10 的整数。")
        elif action == "read":
            if set(args) != {"doc_id"}:
                raise DecisionError("invalid_arguments", "read 仅接受 doc_id。")
            args = {"doc_id": _text(args["doc_id"], "doc_id", 100)}
        else:
            required = {"status", "citations", "gaps"} if action == "finish" else {"question", "citations", "gaps"}
            if set(args) != required:
                raise DecisionError("invalid_arguments", "终止动作参数必须包含 " + "、".join(sorted(required)) + "。")
        return action, args, reason

    def _validate_completion(self, action, args):
        status = "needs_clarification" if action == "clarify" else args["status"]
        if not isinstance(status, str) or status not in {"answered", "conflict", "insufficient", "needs_clarification"}:
            raise DecisionError("invalid_status", "无效的最终状态。")
        if action == "finish" and status == "needs_clarification":
            raise DecisionError("invalid_status", "追问请使用 clarify 动作。")
        raw_citations = args["citations"]
        if not isinstance(raw_citations, list) or len(raw_citations) > 10:
            raise DecisionError("invalid_citations", "citations 必须是至多 10 项的数组。")
        citations = []
        seen = set()
        for item in raw_citations:
            if not isinstance(item, dict) or set(item) != {"doc_id", "quote"}:
                raise DecisionError("invalid_citations", "每个引用必须仅包含 doc_id、quote。")
            doc_id = _text(item["doc_id"], "doc_id", 100)
            quote = _text(item["quote"], "quote", 10000)
            doc = self._read.get(doc_id)
            if doc is None:
                raise DecisionError("unread_citation", f"{doc_id} 尚未成功读取原文，不能引用搜索摘要。")
            if quote not in doc["content"] or not _whole_quote(doc["content"], quote):
                raise DecisionError("invalid_quote", f"{doc_id} 引用必须逐字对应原文完整句子，保留范围和限制。")
            if (doc_id, quote) in seen:
                raise DecisionError("duplicate_citation", "引用重复。")
            seen.add((doc_id, quote))
            citations.append({key: doc[key] for key in ("doc_id", "title", "version_date")} | {"quote": quote})
        gaps = args["gaps"]
        if not isinstance(gaps, list) or len(gaps) > 10:
            raise DecisionError("invalid_gaps", "gaps 必须是至多 10 项的数组。")
        gaps = [_text(gap, "gap", 500) for gap in gaps]
        clarification = _text(args["question"], "question", 1000) if action == "clarify" else ""
        if status in {"answered", "conflict"} and not citations:
            raise DecisionError("missing_evidence", "回答或报告冲突必须有已核验原文引用。")
        if status == "answered" and gaps:
            raise DecisionError("unresolved_gaps", "仍有资料缺口时不能标记为 answered。")
        if status in {"conflict", "insufficient", "needs_clarification"} and not gaps:
            raise DecisionError("missing_gaps", "请明确描述尚缺资料或待确认的适用范围。")
        if status == "conflict":
            if len(citations) < 2:
                raise DecisionError("missing_conflict_citation", "报告冲突必须引用至少两条不同的已读原文，展示冲突双方。")
            clarification = "请确认冲突资料的适用范围及优先级；仅凭版本日期不能选择其中一个答案。"
        if status == "insufficient" and not any(event["action"] == "search" for event in self._trace):
            raise DecisionError("search_required", "判定资料不足前至少需要检索一次；问题本身含糊可使用 clarify。")
        return status, citations, gaps, clarification

    def _maybe_fail(self, action):
        remaining = self._failures.get(action, 0)
        if remaining:
            if remaining > 0:
                self._failures[action] -= 1
            raise ToolError("injected_failure", f"{action} 工具故障（测试注入）。", retryable=True)

    def _record(self, step, action, arguments, reason, result):
        if "error" in result:
            summary = f"失败 {result['error']['code']}：{result['error']['message']}"
        elif action == "search":
            summary = f"命中 {result.get('total', len(result.get('hits', [])))} 份文档；返回：" + ", ".join(hit["doc_id"] for hit in result.get("hits", []))
        elif action == "read":
            summary = f"读取 {result['doc_id']}（{result['version_date']}），原文 {len(result['content'])} 字符。"
        else:
            summary = f"输出通过校验，状态 {result.get('status', 'unknown')}。"
        self._trace.append({
            "step": step, "action": action, "arguments": copy.deepcopy(arguments),
            "reason": reason, "observation_summary": summary, "result": copy.deepcopy(result),
        })

    def _result(self, status, citations, gaps, clarification, termination_reason, started):
        evidence = "\n".join(f"[{c['doc_id']} · {c['version_date']}] {c['quote']}" for c in citations)
        if status == "answered":
            answer = "根据已核验原文：\n" + evidence
        elif status == "conflict":
            answer = "资料存在冲突，无法确定唯一答案：\n" + evidence + "\n" + clarification
        elif status == "needs_clarification":
            answer = clarification + ("\n已核验资料：\n" + evidence if evidence else "")
        elif status == "insufficient":
            answer = "现有资料不足以回答。" + ("\n已核验资料：\n" + evidence if evidence else "")
        elif status == "incomplete":
            answer = "执行达到上限，任务未完成；请调整执行预算或缩小问题范围后重试。"
        else:
            answer = "模型服务异常，任务未完成。"
        return {
            "schema_version": "1.0", "question": self._question, "history": self._history,
            "answer": answer, "status": status, "citations": citations, "gaps": gaps,
            "clarification": clarification, "termination_reason": termination_reason,
            "steps": len(self._trace), "tool_calls": self._tool_calls,
            "limits": {"max_steps": self.max_steps, "max_tool_calls": self.max_tool_calls},
            "model": {"mode": getattr(self.model, "mode", "unknown"), "name": getattr(self.model, "name", type(self.model).__name__)},
            "validation_scope": getattr(self.model, "validation_scope", "模型根据工具观测选择动作；结果仍需结合引用和场景验收。"),
            "usage": self._usage, "started_at": self._started_at,
            "model_responses": self._model_responses,
            "duration_ms": round((time.monotonic() - started) * 1000), "trace": self._trace,
        }
