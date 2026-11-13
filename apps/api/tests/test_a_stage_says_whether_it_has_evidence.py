"""A stage tells the surface whether 「보기」 has anything to show, evidence sent or not."""

from app.pipeline.stage import StepRecord, shows_evidence


def test_a_stage_without_readable_evidence_says_so():
    bare = StepRecord(stage="draft", kind="model", status="done")
    assert bare.stage_wire(evidence=False)["hasEvidence"] is False
    # keys the surface does not show count as nothing to read
    noise = StepRecord(stage="draft", kind="model", status="done",
                       evidence={"findings": {"before": [], "after": []}})
    assert noise.stage_wire()["hasEvidence"] is False


def test_a_stage_with_evidence_says_so_even_without_sending_it():
    calc = StepRecord(stage="calc", kind="compute", status="done",
                      evidence={"computed": [{"name": "총 사업비", "value": "2억 4천만 원"}]})
    wire = calc.stage_wire(evidence=False)
    assert wire["hasEvidence"] is True and "evidence" not in wire
    assert shows_evidence({"findings": {"after": ["ungrounded_value"]}})
    assert not shows_evidence(None)
