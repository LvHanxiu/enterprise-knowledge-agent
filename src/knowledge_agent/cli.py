import argparse
import json
import os
import sys
import unittest
from pathlib import Path

from .agent import Agent
from .models import MockModel, ModelError, OpenAICompatibleModel
from .retrieval import KnowledgeBase, ToolError


SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = SOURCE_ROOT if (SOURCE_ROOT / "data").is_dir() else Path(sys.prefix) / "share" / "enterprise-knowledge-agent"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_env(path):
    if not path:
        return
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("环境文件格式错误，应为 NAME=VALUE。")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "a").isalnum() or key[0].isdigit():
            raise ValueError("环境变量名称无效。")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def create_model(args, scenario=None):
    if args.mode == "mock":
        scenario = scenario or args.mock_scenario
        if not scenario:
            raise ValueError("Mock 仅用于显式场景流程测试，请指定 --mock-scenario，例如 conflict；通用问答请用 --mode live。")
        return MockModel(scenario)
    key_name = args.api_key_env or "OPENAI_API_KEY"
    key = os.environ.get(key_name, "")
    if not key:
        raise ValueError(f"live 模式缺少环境变量 {key_name}；离线演示请显式使用 --mode mock。")
    return OpenAICompatibleModel(
        api_key=key,
        model=args.model or os.environ.get("KNOWLEDGE_MODEL", "gpt-4.1-mini"),
        base_url=args.base_url or os.environ.get("KNOWLEDGE_BASE_URL", "https://api.openai.com/v1"),
        timeout=args.timeout,
    )


def add_shared(parser):
    parser.add_argument("--mode", choices=["live", "mock"], default="live")
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "knowledge_base.json")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--max-tool-calls", type=int, default=8)


def build_parser():
    parser = argparse.ArgumentParser(description="企业知识问答 Agent：检索、原文核验、动态决策和可审计引用")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="回答一个问题")
    add_shared(ask)
    ask.add_argument("--mock-scenario", help="离线流程场景，如 normal、conflict、context；不验证自然语言理解")
    ask.add_argument("question", nargs="?")
    ask.add_argument("--input", type=Path, help="包含 question 和可选 history 的 JSON 文件")
    ask.add_argument("--history", type=Path, help="user/assistant 消息数组 JSON")
    ask.add_argument("--output", type=Path)
    ask.add_argument("--trace", type=Path)
    ask.add_argument("--fail-search", type=int, default=0, help="测试注入失败次数；-1 持续失败")
    ask.add_argument("--fail-read", type=int, default=0, help="测试注入失败次数；-1 持续失败")
    evaluate = commands.add_parser("evaluate", help="执行验收用例并保存输入、输出和 Trace")
    add_shared(evaluate)
    evaluate.add_argument("--cases", type=Path, default=ROOT / "examples" / "cases.json")
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--case", action="append", dest="case_ids", help="仅执行指定用例，可多次提供")
    commands.add_parser("test", help="运行全部离线自动化测试")
    return parser


def save_trace(path, trace):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in trace), encoding="utf-8")


def check_expected(result, expected):
    checks = {}
    if "status" in expected:
        statuses = expected["status"] if isinstance(expected["status"], list) else [expected["status"]]
        checks["status"] = result["status"] in statuses
    for word in expected.get("answer_contains", []):
        checks["answer_contains:" + word] = word in result["answer"]
    for word in expected.get("answer_excludes", []):
        checks["answer_excludes:" + word] = word not in result["answer"]
    for doc_id in expected.get("citation_ids", []):
        checks["citation:" + doc_id] = any(c["doc_id"] == doc_id for c in result["citations"])
    for action in expected.get("actions", []):
        checks["action:" + action] = any(event["action"] == action and "error" not in event["result"] for event in result["trace"])
    if expected.get("has_tool_error"):
        checks["tool_error_observed"] = any(event["action"] in {"search", "read"} and "error" in event["result"] for event in result["trace"])
    checks["bounded_execution"] = result["steps"] <= result["limits"]["max_steps"] and result["tool_calls"] <= result["limits"]["max_tool_calls"]
    checks["required_evidence"] = bool(result["citations"]) if result["status"] in {"answered", "conflict"} else True
    checks["explicit_gaps"] = bool(result["gaps"]) if result["status"] != "answered" else not result["gaps"]
    checks["citations_verified"] = all(any(
        event["action"] == "read" and event["result"].get("doc_id") == citation["doc_id"]
        and citation["quote"] in event["result"].get("content", "")
        for event in result["trace"]
    ) for citation in result["citations"])
    return checks


def evaluate_cases(args):
    cases = read_json(args.cases)
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases 必须是非空用例数组。")
    seen_ids = set()
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict) or not {"id", "question", "expected"} <= case.keys():
            raise ValueError(f"第 {index} 个用例必须包含 id、question、expected。")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("用例 ID 只能包含字母、数字、下划线或连字符。")
        if case_id in seen_ids:
            raise ValueError("用例 ID 不能重复。")
        seen_ids.add(case_id)
        if not isinstance(case["question"], str) or not case["question"].strip() or not isinstance(case["expected"], dict):
            raise ValueError(f"用例 {case_id} 的 question 必须是非空字符串，expected 必须是对象。")
    if args.case_ids:
        missing = set(args.case_ids) - {case["id"] for case in cases}
        if missing:
            raise ValueError("未知用例：" + ", ".join(sorted(missing)))
        cases = [case for case in cases if case["id"] in args.case_ids]
    live_model = create_model(args) if args.mode == "live" else None
    rows = []
    for case in cases:
        case_id = case["id"]
        model = live_model or create_model(args, case.get("mock_scenario", case_id))
        case_data = ROOT / case["data"] if "data" in case else args.data
        agent = Agent(model, KnowledgeBase(case_data),
                      max_steps=case.get("max_steps", args.max_steps),
                      max_tool_calls=case.get("max_tool_calls", args.max_tool_calls),
                      failures=case.get("failures"))
        result = agent.run(case["question"], case.get("history"))
        checks = check_expected(result, case["expected"])
        record = {
            "id": case_id, "passed": all(checks.values()), "checks": checks,
            "expected": case["expected"], "actual_status": result["status"],
            "actions": [event["action"] for event in result["trace"]],
            "steps": result["steps"], "tool_calls": result["tool_calls"],
            "output": case_id + ".output.json", "trace": case_id + ".trace.jsonl",
        }
        rows.append(record)
        write_json(args.output_dir / (case_id + ".input.json"), case)
        write_json(args.output_dir / (case_id + ".output.json"), result)
        save_trace(args.output_dir / (case_id + ".trace.jsonl"), result["trace"])
        print(f"{case_id}: {result['status']} ({'PASS' if record['passed'] else 'FAIL'})", file=sys.stderr)
    report = {
        "mode": args.mode, "model": live_model.name if live_model else "explicit-offline-scenarios",
        "validation_scope": "真实模型决策与流程" if args.mode == "live" else "仅运行所选离线场景，不验证自然语言理解或模型决策能力",
        "total": len(rows), "passed": sum(row["passed"] for row in rows), "cases": rows,
    }
    write_json(args.output_dir / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] == report["total"] else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "test":
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
            return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
        load_env(args.env_file)
        if args.command == "evaluate":
            return evaluate_cases(args)
        if args.input and (args.question or args.history):
            raise ValueError("--input 不能同时搭配位置参数问题或 --history。")
        if args.input:
            payload = read_json(args.input)
            if not isinstance(payload, dict) or not {"question"} <= set(payload) <= {"question", "history"}:
                raise ValueError("输入 JSON 必须仅包含 question 和可选 history。")
            question, history = payload["question"], payload.get("history")
        else:
            question = args.question
            history = read_json(args.history) if args.history else None
        model = create_model(args)
        result = Agent(model, KnowledgeBase(args.data), args.max_steps, args.max_tool_calls,
                       {"search": args.fail_search, "read": args.fail_read}).run(question, history)
        if args.output:
            write_json(args.output, result)
        if args.trace:
            save_trace(args.trace, result["trace"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result["status"] == "incomplete" else 3 if result["status"] == "error" else 0
    except (ValueError, OSError, ModelError, ToolError) as exc:
        if isinstance(exc, OSError):
            message = "文件读写失败，请检查路径和权限。"
        elif isinstance(exc, json.JSONDecodeError):
            message = "输入 JSON 格式错误。"
        else:
            message = str(exc)
        print(json.dumps({"status": "error", "error": message}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
