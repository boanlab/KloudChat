"""A shipped workflow is shown as a few plain steps: each pipeline file says what its chain's
steps do, and every check it names has a person's name (never only its id)."""

import re

from app.pipeline import registry
from app.routers.pipelines import describe
from app.services import check_skills

CHAIN = {"material", "question", "compute", "write", "check", "export"}
HANGUL = re.compile(r"[가-힣]")


def test_every_pipeline_says_what_its_steps_do():
    for pipe in registry.pipelines().values():
        assert pipe.summary, f"{pipe.name}: no [summary]"
        assert set(pipe.summary) <= CHAIN, f"{pipe.name}: {set(pipe.summary) - CHAIN}"
        for step, line in pipe.summary.items():
            assert HANGUL.search(line) and len(line) <= 60, f"{pipe.name}.{step}: {line!r}"


def test_checks_have_peoples_names():
    for pipe in registry.pipelines().values():
        for key in (None, *pipe.agents):
            out = describe(pipe, check_skills.attached(agent_key=key))
            assert out["summary"] == pipe.summary
            for check, name in out["checkNames"].items():
                assert HANGUL.search(name), f"{pipe.name}: {check} has no Korean name"


def test_summary_is_display_only():
    # The stage lists are what they were: the summary adds no flow and no stage.
    minutes = registry.pipeline("minutes")
    assert "summary" not in minutes.flows
    assert minutes.flow("pre") == ("check:gaps", "gap-card")
