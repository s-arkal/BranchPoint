from branchpoint.core.provenance import ProvenanceTracker


def test_basic_sidecar_provenance():
    tracker = ProvenanceTracker()
    value = {"x": 1}

    tracker.attach(value, "evt_1")

    assert tracker.event_ids(value) == ["evt_1"]


def test_recursive_provenance():
    tracker = ProvenanceTracker()
    child = {"x": 1}
    parent = {"child": child}

    tracker.attach(child, "evt_child")

    assert tracker.event_ids(parent) == ["evt_child"]
