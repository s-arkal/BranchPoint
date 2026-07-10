from pathlib import Path
import tomllib

from branchpoint import Adapter, FrameworkAdapter, ModelProviderWrapper, ProviderCallContext
from branchpoint.adapters import (
    ADAPTER_DESIGN_RULES,
    LANGGRAPH_ADAPTER_PREREQUISITES,
    OPENTELEMETRY_MAPPING,
    AdapterRunContext,
)
from branchpoint.core.schema import LLM_CALL, LLM_OUTPUT, SUCCESS, TraceEvent, TraceRun, USER_REQUEST, utc_now_iso
from branchpoint.providers import PROVIDER_WRAPPER_RULES


class FakeProviderWrapper:
    provider_name = "fake-provider"

    def wrap(self, client):
        return client

    def record_request(self, request, *, context=None):
        return TraceEvent(
            event_id="evt_provider_request",
            run_id=context.run_id or "run_provider" if context else "run_provider",
            project_id="demo",
            type=LLM_CALL,
            input=request,
            metadata={"provider": context.provider if context else self.provider_name},
        )

    def record_response(self, response, *, request_event=None, context=None):
        input_refs = [request_event.event_id] if request_event is not None else []
        return TraceEvent(
            event_id="evt_provider_response",
            run_id=request_event.run_id if request_event is not None else "run_provider",
            project_id="demo",
            type=LLM_OUTPUT,
            output=response,
            input_refs=input_refs,
            metadata={"provider": context.provider if context else self.provider_name},
        )


class FakeFrameworkAdapter:
    adapter_name = "fake-framework"

    def start_run(self, context):
        return TraceRun(
            run_id=context.run_id or "run_adapter",
            project_id=context.project_id,
            name=context.name,
            started_at=utc_now_iso(),
        )

    def record_event(self, native_event):
        return self.to_trace_event(native_event)

    def end_run(self, context):
        return TraceRun(
            run_id=context.run_id or "run_adapter",
            project_id=context.project_id,
            name=context.name,
            started_at=utc_now_iso(),
            ended_at=utc_now_iso(),
            status=SUCCESS,
        )

    def to_trace_event(self, native_event):
        return TraceEvent(
            event_id=native_event["event_id"],
            run_id=native_event["run_id"],
            project_id=native_event["project_id"],
            type=native_event.get("type", USER_REQUEST),
            name=native_event.get("name"),
            metadata={"native": native_event.get("native", {})},
        )


def test_provider_wrapper_protocol_is_importable_and_structural():
    wrapper = FakeProviderWrapper()
    context = ProviderCallContext(provider="fake-provider", operation="chat.completions", model="demo", run_id="run_1")
    client = object()

    assert isinstance(wrapper, ModelProviderWrapper)
    assert wrapper.wrap(client) is client

    request_event = wrapper.record_request({"messages": []}, context=context)
    response_event = wrapper.record_response({"content": "hello"}, request_event=request_event, context=context)

    assert request_event.type == LLM_CALL
    assert response_event.type == LLM_OUTPUT
    assert response_event.input_refs == [request_event.event_id]
    assert "preserve normal client return values" in PROVIDER_WRAPPER_RULES[0]


def test_framework_adapter_protocol_replaces_marker_adapter_compatibly():
    adapter = FakeFrameworkAdapter()
    context = AdapterRunContext(project_id="demo", name="workflow", run_id="run_1")
    native_event = {
        "event_id": "evt_native",
        "run_id": "run_1",
        "project_id": "demo",
        "type": USER_REQUEST,
        "name": "native_request",
    }

    assert Adapter is FrameworkAdapter
    assert isinstance(adapter, FrameworkAdapter)
    assert isinstance(adapter, Adapter)

    run = adapter.start_run(context)
    event = adapter.record_event(native_event)
    ended = adapter.end_run(context)

    assert run.run_id == "run_1"
    assert event.type == USER_REQUEST
    assert event.name == "native_request"
    assert ended.status == SUCCESS


def test_adapter_design_docs_cover_otel_langgraph_and_scope_boundaries():
    assert "TraceEvent.span_id" in OPENTELEMETRY_MAPPING["span_id"]
    assert any("explicit" in rule for rule in ADAPTER_DESIGN_RULES)
    assert any("optional" in rule for rule in LANGGRAPH_ADAPTER_PREREQUISITES)
    assert any("scoring" in rule for rule in ADAPTER_DESIGN_RULES)


def test_phase_08_does_not_add_mandatory_provider_or_framework_dependencies():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = "\n".join(pyproject["project"].get("dependencies", [])).lower()
    forbidden = (
        "openai",
        "anthropic",
        "langchain",
        "langgraph",
        "crewai",
        "pydantic-ai",
        "opentelemetry",
        "sglang",
        "neo4j",
    )

    assert not any(name in dependencies for name in forbidden)
    assert not Path("branchpoint/scorer.py").exists()
