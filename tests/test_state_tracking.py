import pytest

from branchpoint import BranchPoint
from branchpoint.core.errors import EventContractError
from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.core.graph_types import EDGE_SOURCE_STATE_PATH_MATCH, EXPLICIT_INPUT_REF, STATE_DEPENDENCY
from branchpoint.core.schema import (
    METADATA_AFTER_HASH,
    METADATA_BEFORE_HASH,
    METADATA_OPERATION,
    METADATA_STATE_NAME,
    METADATA_STATE_PATH,
    METADATA_VALUE_HASH,
    STATE_READ,
    STATE_WRITE,
    canonical_state_path,
)


def test_state_read_and_write_events_use_canonical_metadata(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        write = bp.state_write(
            ["customer", "plan/name~tier"],
            before=None,
            after="gold",
            metadata={"state_name": "refund_case", "source": "tool"},
        )
        read = bp.state_read(
            "/customer/plan~1name~0tier",
            value="gold",
            state_name="refund_case",
        )

    events = bp.store.list_events(trace.run_id)

    assert [event.type for event in events] == [STATE_WRITE, STATE_READ]
    assert write.metadata[METADATA_STATE_PATH] == "/customer/plan~1name~0tier"
    assert read.metadata[METADATA_STATE_PATH] == "/customer/plan~1name~0tier"
    assert write.metadata[METADATA_STATE_NAME] == "refund_case"
    assert read.metadata[METADATA_STATE_NAME] == "refund_case"
    assert write.metadata[METADATA_OPERATION] == "write"
    assert read.metadata[METADATA_OPERATION] == "read"
    assert write.metadata["source"] == "tool"
    assert write.metadata[METADATA_BEFORE_HASH]
    assert write.metadata[METADATA_AFTER_HASH]
    assert read.metadata[METADATA_VALUE_HASH]


def test_state_path_canonicalization_rejects_malformed_pointer(tmp_path):
    assert canonical_state_path([]) == ""
    assert canonical_state_path(["a/b", "c~d", 0]) == "/a~1b/c~0d/0"
    assert canonical_state_path("/a~1b/c~0d/0") == "/a~1b/c~0d/0"

    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))
    with bp.trace("run"):
        with pytest.raises(EventContractError):
            bp.state_read("customer/plan", value="gold")
        with pytest.raises(EventContractError):
            bp.state_read("/customer/~bad", value="gold")


def test_graph_infers_exact_state_dependency(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        write = bp.state_write("/refund/eligible", before=None, after=True, state_name="refund_case")
        read = bp.state_read("/refund/eligible", value=True, state_name="refund_case")

    graph = GraphBuilder(bp.store).build(trace.run_id)
    edge = _single_edge(graph, write.event_id, read.event_id, STATE_DEPENDENCY)

    assert edge["metadata"]["source_kind"] == EDGE_SOURCE_STATE_PATH_MATCH
    assert edge["metadata"]["state_name"] == "refund_case"
    assert edge["metadata"]["state_path"] == "/refund/eligible"
    assert edge["metadata"]["path_match"] == "exact"


def test_graph_infers_nested_state_dependency_from_parent_write(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        write = bp.state_write("/refund", before=None, after={"eligible": False}, state_name="refund_case")
        read = bp.state_read("/refund/eligible", value=False, state_name="refund_case")

    graph = GraphBuilder(bp.store).build(trace.run_id)
    edge = _single_edge(graph, write.event_id, read.event_id, STATE_DEPENDENCY)

    assert edge["metadata"]["path_match"] == "nested"
    assert edge["metadata"]["write_state_path"] == "/refund"
    assert edge["metadata"]["read_state_path"] == "/refund/eligible"


def test_state_dependencies_do_not_cross_namespaces_or_time(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        early_read = bp.state_read("/refund/eligible", value=None, state_name="refund_case")
        write = bp.state_write("/refund/eligible", before=None, after=True, state_name="refund_case")
        other_namespace_read = bp.state_read("/refund/eligible", value=True, state_name="planner_state")
        later_read = bp.state_read("/refund/eligible", value=True, state_name="refund_case")

    graph = GraphBuilder(bp.store).build(trace.run_id)

    assert not _has_edge(graph, write.event_id, early_read.event_id, STATE_DEPENDENCY)
    assert not _has_edge(graph, write.event_id, other_namespace_read.event_id, STATE_DEPENDENCY)
    assert _has_edge(graph, write.event_id, later_read.event_id, STATE_DEPENDENCY)


def test_state_path_match_does_not_duplicate_explicit_input_ref_dependency(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        write = bp.state_write("/refund/eligible", before=None, after=True, state_name="refund_case")
        read = bp.state_read(
            "/refund/eligible",
            value=True,
            state_name="refund_case",
            input_refs=[write.event_id],
        )

    graph = GraphBuilder(bp.store).build(trace.run_id)

    assert len(_edges(graph, write.event_id, read.event_id, STATE_DEPENDENCY)) == 1
    assert _has_edge(graph, write.event_id, read.event_id, EXPLICIT_INPUT_REF)


def test_explicit_state_dependency_suppresses_duplicate_state_path_match(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        write = bp.state_write("/refund/eligible", before=None, after=True, state_name="refund_case")
        read = bp.state_read("/refund/eligible", value=True, state_name="refund_case")
        explicit = bp.edge(
            write.event_id,
            read.event_id,
            STATE_DEPENDENCY,
            reason="domain declared state dependency",
        )

    graph = GraphBuilder(bp.store).build(trace.run_id)
    edges = _edges(graph, write.event_id, read.event_id, STATE_DEPENDENCY)

    assert len(edges) == 1
    assert edges[0]["metadata"]["explicit"] is True
    assert explicit.edge_id in graph[write.event_id][read.event_id]


def test_multiple_explicit_state_dependencies_survive_graph_rebuild(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        write = bp.state_write("/refund/eligible", before=None, after=True, state_name="refund_case")
        read = bp.state_read("/refund/eligible", value=True, state_name="refund_case")
        first = bp.edge(
            write.event_id,
            read.event_id,
            STATE_DEPENDENCY,
            reason="domain declared state dependency",
        )
        second = bp.edge(
            write.event_id,
            read.event_id,
            STATE_DEPENDENCY,
            reason="adapter confirmed state dependency",
        )

    graph = GraphBuilder(bp.store).build(trace.run_id)
    edges = _edges(graph, write.event_id, read.event_id, STATE_DEPENDENCY)
    edge_ids = set(graph[write.event_id][read.event_id])

    assert len(edges) == 2
    assert first.edge_id in edge_ids
    assert second.edge_id in edge_ids
    assert all(edge["metadata"]["explicit"] is True for edge in edges)


def test_state_reader_decorator_records_read_and_attaches_provenance(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.state_reader("/customer/plan", state_name="refund_case")
    def load_plan(state):
        return state["customer"]["plan"]

    with bp.trace("run") as trace:
        plan = load_plan({"customer": {"plan": "gold"}})
        consumer = bp.emit(type="llmcall", name="consumer", input={"plan": plan})

    read = next(event for event in bp.store.list_events(trace.run_id) if event.type == STATE_READ)

    assert read.metadata[METADATA_STATE_NAME] == "refund_case"
    assert read.metadata[METADATA_STATE_PATH] == "/customer/plan"
    assert read.event_id in consumer.input_refs


def _has_edge(graph, source: str, target: str, edge_type: str) -> bool:
    return bool(_edges(graph, source, target, edge_type))


def _single_edge(graph, source: str, target: str, edge_type: str) -> dict:
    edges = _edges(graph, source, target, edge_type)
    assert len(edges) == 1
    return edges[0]


def _edges(graph, source: str, target: str, edge_type: str) -> list[dict]:
    edge_data = graph.get_edge_data(source, target) or {}
    return [data for data in edge_data.values() if data["edge_type"] == edge_type]
