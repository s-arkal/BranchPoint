import json

from branchpoint import BranchPoint
from branchpoint.cli.main import main
from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.core.graph_types import GraphEdge, STATE_DEPENDENCY, TOOL_RESULT_DEPENDENCY
from branchpoint.core.schema import FAILURE_LABEL, FINAL_OUTPUT, LLM_CALL, TOOL_OUTPUT, USER_REQUEST


def test_graph_build_metadata_export_and_path_utilities(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        request = bp.emit(type=USER_REQUEST, name="request", auto_refs=False)
        tool_output = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        llm_call = bp.emit(type=LLM_CALL, name="interpret", input_refs=[tool_output.event_id], auto_refs=False)
        final = bp.emit(
            type=FINAL_OUTPUT,
            name="answer",
            input_refs=[request.event_id, llm_call.event_id],
            auto_refs=False,
        )
        failure = bp.emit(type=FAILURE_LABEL, name="failure", input_refs=[final.event_id], auto_refs=False)

    builder = GraphBuilder(bp.store)
    builder.build(trace.run_id)
    builder.build(trace.run_id)
    builds = bp.store.list_graph_builds(trace.run_id)
    exported = builder.export_json(trace.run_id)

    assert len(builds) == 2
    assert builds[-1].builder_version == builder.builder_version
    assert builds[-1].rule_version == builder.rule_version
    assert builds[-1].metadata["event_count"] == 5
    assert exported["schema_version"] == "branchpoint.graph_export.v1"
    assert exported["build"]["status"] == "success"
    assert {node["event_id"] for node in exported["nodes"]} == {
        request.event_id,
        tool_output.event_id,
        llm_call.event_id,
        final.event_id,
        failure.event_id,
    }
    assert any(edge["edge_type"] == TOOL_RESULT_DEPENDENCY for edge in exported["edges"])

    assert failure.event_id in builder.downstream_dependents(trace.run_id, tool_output.event_id)
    assert tool_output.event_id in builder.upstream_evidence(trace.run_id, failure.event_id)
    assert tool_output.event_id in builder.ancestors_of_failure(trace.run_id, failure.event_id)
    assert [tool_output.event_id, llm_call.event_id, final.event_id, failure.event_id] in builder.paths_to_failure(
        trace.run_id,
        tool_output.event_id,
        failure.event_id,
    )


def test_validate_graph_reports_broken_refs_endpoints_and_missing_edge_provenance(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    with bp.trace("run") as trace:
        source = bp.emit(type=TOOL_OUTPUT, name="lookup", auto_refs=False)
        bp.emit(type=LLM_CALL, name="interpret", input_refs=["evt_missing"], auto_refs=False)

    bp.store.append_edge(
        GraphEdge(
            edge_id="edge_broken",
            run_id=trace.run_id,
            source_event_id=source.event_id,
            target_event_id="evt_missing",
            edge_type=STATE_DEPENDENCY,
            metadata={},
        )
    )

    report = GraphBuilder(bp.store).validate_graph(trace.run_id)
    codes = {error["code"] for error in report["errors"]}

    assert report["status"] == "fail"
    assert {"broken_event_ref", "invalid_edge_target", "missing_edge_provenance"} <= codes


def test_cli_json_inspection_payload_snapshots_and_validation(tmp_path, capsys):
    db_path = tmp_path / ".branchpoint" / "branchpoint.sqlite"
    bp = BranchPoint(project="demo", db_path=str(db_path), max_inline_bytes=32)

    with bp.trace("run") as trace:
        event = bp.emit(type=USER_REQUEST, name="request", output={"text": "x" * 200})
        snapshot = bp.snapshot(event_id=event.event_id, payload={"answer": 42})

    assert main(["--db", str(db_path), "graph", trace.run_id, "--json"]) == 0
    graph_json = json.loads(capsys.readouterr().out)
    assert graph_json["run_id"] == trace.run_id
    assert graph_json["build"]["metadata"]["event_count"] == 1

    assert main(["--db", str(db_path), "event", event.event_id, "--json"]) == 0
    event_json = json.loads(capsys.readouterr().out)
    assert event_json["event_id"] == event.event_id
    assert event_json["output_payload_ref"] is not None

    assert main(["--db", str(db_path), "payload", event.event_id, "--output"]) == 0
    assert json.loads(capsys.readouterr().out) == {"text": "x" * 200}

    assert main(["--db", str(db_path), "snapshots", trace.run_id, "--json"]) == 0
    snapshots_json = json.loads(capsys.readouterr().out)
    assert snapshots_json[0]["snapshot_id"] == snapshot.snapshot_id

    assert main(["--db", str(db_path), "snapshot", snapshot.snapshot_id, "--payload"]) == 0
    snapshot_json = json.loads(capsys.readouterr().out)
    assert snapshot_json["payload"] == {"answer": 42}

    assert main(["--db", str(db_path), "validate-graph", trace.run_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"

    assert main(["--db", str(db_path), "validate-run", trace.run_id]) == 0
    assert "validation passed" in capsys.readouterr().out
