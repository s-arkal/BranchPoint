import json

from branchpoint import BranchPoint
from branchpoint.core.provenance import ProvenanceTracker
from branchpoint.core.proxies import TrackedDict, TrackedScalar, TrackedString, unwrap, wrap_tracked
from branchpoint.core.refs import ProvenanceRef
from branchpoint.core.serialization import safe_serialize


def test_tracked_dict_field_read():
    tracker = ProvenanceTracker()
    payment = wrap_tracked({"refund_eligible": True}, [ProvenanceRef("evt_tool")], tracker)

    eligible = payment["refund_eligible"]

    assert isinstance(payment, TrackedDict)
    assert isinstance(eligible, TrackedScalar)
    assert tracker.event_ids(eligible) == ["evt_tool"]
    assert tracker.details(eligible)[0]["path"] == ["refund_eligible"]


def test_tracked_dict_get_preserves_refs():
    tracker = ProvenanceTracker()
    payment = wrap_tracked({"status": "approved"}, [ProvenanceRef("evt_tool")], tracker)

    status = payment.get("status")

    assert isinstance(status, TrackedString)
    assert tracker.event_ids(status) == ["evt_tool"]
    assert tracker.details(status)[0]["path"] == ["status"]
    assert payment.get("missing", "fallback") == "fallback"


def test_tracked_dict_items_values_preserve_refs():
    tracker = ProvenanceTracker()
    payment = wrap_tracked({"status": "approved", "amount": 42}, [ProvenanceRef("evt_tool")], tracker)

    items = list(payment.items())
    values = list(payment.values())

    assert [key for key, _ in items] == ["status", "amount"]
    assert tracker.event_ids(items[0][1]) == ["evt_tool"]
    assert tracker.details(items[0][1])[0]["path"] == ["status"]
    assert tracker.event_ids(values[1]) == ["evt_tool"]
    assert tracker.details(values[1])[0]["path"] == ["amount"]


def test_tracked_list_index_preserves_refs():
    tracker = ProvenanceTracker()
    values = wrap_tracked(["first", "second"], [ProvenanceRef("evt_list")], tracker)

    item = values[1]

    assert str(item) == "second"
    assert tracker.event_ids(item) == ["evt_list"]
    assert tracker.details(item)[0]["path"] == [1]


def test_tracked_scalar_bool_and_eq():
    tracker = ProvenanceTracker()
    eligible = wrap_tracked(True, [ProvenanceRef("evt_tool")], tracker)
    amount = wrap_tracked(42, [ProvenanceRef("evt_tool")], tracker)

    assert bool(eligible) is True
    assert eligible == True
    assert amount == 42
    assert repr(amount) == "42"
    assert tracker.event_ids(eligible) == ["evt_tool"]


def test_tracked_string_basic_methods_keep_refs():
    tracker = ProvenanceTracker()
    status = wrap_tracked(" Approved ", [ProvenanceRef("evt_tool")], tracker)

    stripped = status.strip()
    lower = stripped.lower()
    split = lower.split("v")

    assert str(lower) == "approved"
    assert "prov" in lower
    assert tracker.event_ids(stripped) == ["evt_tool"]
    assert tracker.event_ids(lower) == ["evt_tool"]
    assert tracker.event_ids(split[0]) == ["evt_tool"]


def test_unwrap_and_detach_return_plain_data(tmp_path):
    bp = BranchPoint(project="demo", db_path=str(tmp_path / "branchpoint.sqlite"))

    @bp.tool("lookup")
    def lookup():
        return {"status": "approved", "items": [1, 2]}

    with bp.trace("run"):
        payment = lookup()
        plain = bp.unwrap(payment)
        detached = bp.detach(payment)

        assert plain == {"status": "approved", "items": [1, 2]}
        assert detached == {"status": "approved", "items": [1, 2]}
        assert type(plain) is dict
        assert type(plain["items"]) is list
        assert bp.refs(detached) == []


def test_safe_serialize_omits_proxy_internals():
    tracker = ProvenanceTracker()
    payment = wrap_tracked({"status": "approved"}, [ProvenanceRef("evt_tool")], tracker)

    serialized = safe_serialize({"payment": payment, "status": payment["status"]})
    rendered = json.dumps(serialized, sort_keys=True)

    assert serialized == {"payment": {"status": "approved"}, "status": "approved"}
    assert "_bp_" not in rendered
    assert "ProvenanceTracker" not in rendered


def test_mutation_preserves_container_and_inserted_provenance():
    tracker = ProvenanceTracker()
    payment = wrap_tracked({"status": "approved"}, [ProvenanceRef("evt_payment")], tracker)
    inserted = wrap_tracked("manual", [ProvenanceRef("evt_inserted")], tracker)

    payment["note"] = inserted
    payment["status"] = "refunded"

    assert set(tracker.event_ids(payment["status"])) == {"evt_payment"}
    assert set(tracker.event_ids(payment["note"])) == {"evt_payment", "evt_inserted"}
