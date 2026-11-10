from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.execution.plan import PlanError, resolve_load, steps_of
from app.execution.prompting import build_messages, build_request
from app.schemas.run import LoadConfig, RunCreate

LIMITS = {"max_requests": 1000, "max_concurrency": 64, "max_duration_seconds": 3600}


def plan(case_count=10, **load):
    return resolve_load(LoadConfig(**load), case_count=case_count, **LIMITS)


def test_profiles_resolve_to_recorded_steps():
    assert plan(profile="sequential")["steps"] == [
        {"step": None, "concurrency": 1, "start": 0, "count": 10, "duration_seconds": None}
    ]
    assert plan(profile="smoke")["requests"] == 5
    assert plan(case_count=3, profile="smoke")["requests"] == 3
    fixed = plan(profile="fixed_concurrency", requests=50, concurrency=8, warmup_requests=2)
    assert fixed["requests"] == 50 and fixed["concurrency"] == 8 and fixed["warmup_requests"] == 2
    ramp = plan(profile="ramp", ramp_concurrency=[1, 4, 16], requests_per_step=20)
    assert [(s.step, s.concurrency, s.start, s.count) for s in steps_of(ramp)] == [
        (0, 1, 0, 20),
        (1, 4, 20, 20),
        (2, 16, 40, 20),
    ]
    assert ramp["requests"] == 60
    sustained = plan(profile="sustained", concurrency=4, duration_seconds=30)
    assert sustained["requests"] is None
    assert steps_of(sustained)[0].duration_seconds == 30


def test_plan_limits():
    with pytest.raises(PlanError, match="no cases"):
        plan(case_count=0)
    with pytest.raises(PlanError, match="At most 1000"):
        plan(requests=1001)
    with pytest.raises(PlanError, match="Concurrency"):
        plan(concurrency=65)
    with pytest.raises(PlanError, match="duration"):
        plan(profile="sustained", duration_seconds=4000)


@pytest.mark.parametrize(
    ("load", "message"),
    [
        ({"profile": "sequential", "concurrency": 2}, "concurrency 1"),
        ({"profile": "smoke", "requests": 6}, "at most 5"),
        ({"profile": "smoke", "concurrency": 3}, "low concurrency"),
        ({"profile": "ramp", "ramp_concurrency": [1]}, "two concurrency steps"),
        ({"profile": "ramp", "ramp_concurrency": [2, 2]}, "increase"),
        ({"profile": "sustained"}, "duration_seconds"),
        ({"profile": "fixed_concurrency", "duration_seconds": 5}, "sustained profile only"),
        ({"profile": "fixed_concurrency", "ramp_concurrency": [1, 2]}, "ramp profile only"),
    ],
)
def test_load_validation(load, message):
    with pytest.raises(ValidationError, match=message):
        LoadConfig(**load)


def test_only_implemented_suites_are_accepted():
    base = {
        "name": "x",
        "endpoint_ids": ["00000000-0000-0000-0000-000000000001"],
        "datasets": [{"dataset_id": "00000000-0000-0000-0000-000000000002"}],
    }
    assert RunCreate(**base).suites == ["performance"]
    with pytest.raises(ValidationError, match="Milestone 7"):
        RunCreate(**base, suites=["quantization"])
    with pytest.raises(ValidationError, match="Unknown suite"):
        RunCreate(**base, suites=["vibes"])


# -------------------------------------------------------------------------------- prompting


def test_plain_input_and_system_prompt():
    assert build_messages({"type": "qa", "input": "Hi?"}) == [{"role": "user", "content": "Hi?"}]
    msgs = build_messages(
        {
            "type": "chat",
            "system_prompt": "Be brief.",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Yo"},
            ],
        }
    )
    assert msgs[0] == {"role": "system", "content": "You are helpful.\n\nBe brief."}
    assert len(msgs) == 2


def test_rag_long_context_and_structured_prompts():
    rag = build_messages(
        {
            "type": "rag",
            "input": "When?",
            "retrieved_contexts": [
                {"document_id": "d1", "chunk_id": "c2", "text": "In 1999."},
                {"text": "Other."},
            ],
        }
    )
    content = rag[0]["content"]
    assert "[d1#c2]\nIn 1999." in content and "[doc-2]\nOther." in content
    assert content.endswith("Question: When?")

    lc = build_messages({"type": "long_context", "context": "BIG TEXT", "input": "Find it."})
    assert lc[0]["content"] == "BIG TEXT\n\nFind it."

    so = build_messages(
        {"type": "structured_output", "input": "Give JSON", "json_schema": {"type": "object"}}
    )
    assert so[0]["role"] == "system" and '{"type": "object"}' in so[0]["content"]


def test_request_carries_tools_and_generation_overrides():
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    req = build_request(
        {"type": "tool_call", "input": "do", "tools": tools},
        {"temperature": 0, "max_tokens": 9, "seed": 7, "top_p": None},
    )
    assert req.tools == tools and req.temperature == 0 and req.max_tokens == 9
    assert req.extra_body == {"seed": 7} and req.top_p is None
    plain = build_request({"type": "qa", "input": "q", "tools": tools}, {})
    assert plain.tools is None  # tools are only sent for tool-call cases
