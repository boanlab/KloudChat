"""A model's flowchart: its own caption line is not a part, `:::hot` / `:::frozen` marks are
kept in either spelling, a `[( )]` node is a store."""

from __future__ import annotations

from app.services import diagram_shapes as ds

_SOURCE = """flowchart LR
    subgraph enc["인코더"]
        img[입력 영상] --> cnn(CNN 인코더):::frozen
    end
    subgraph loss["손실"]
        hung(헝가리안 매칭)
    end
    cnn --> hung
    store[(벡터 저장소)] -.-> cnn
    caption["그림: 제안 모델의 구조"]
    cnn --> caption
    loss:::hot
    class hung hot
"""


def test_a_caption_line_written_as_a_node_is_dropped_with_its_links():
    graph = ds.parse(_SOURCE)
    assert graph is not None
    assert "caption" not in graph.nodes
    assert not [e for e in graph.edges if "caption" in (e.a, e.b)]
    assert all("제안 모델의 구조" not in n.label for n in graph.nodes.values())


def test_a_lone_class_line_and_a_class_statement_both_mark_a_node():
    graph = ds.parse(_SOURCE)
    assert graph.nodes["hung"].hot
    assert graph.nodes["cnn"].frozen and not graph.nodes["cnn"].hot
    # a mark on a group id is not a node: nothing leaks as a part of the figure
    assert "loss" not in graph.nodes


def test_a_class_statement_marks_several_nodes_at_once():
    graph = ds.parse("flowchart LR\n  a[A] --> b[B] --> c[C]\n  class a,b frozen\n")
    assert graph.nodes["a"].frozen and graph.nodes["b"].frozen and not graph.nodes["c"].frozen


def test_a_store_is_a_cylinder_and_a_decision_a_diamond():
    graph = ds.parse(_SOURCE + "    g{통과?}\n")
    assert graph.nodes["store"].shape == "cylinder"
    assert graph.nodes["g"].shape == "diamond"


def test_one_zone_around_every_node_is_not_kept():
    graph = ds.parse("flowchart TB\n  subgraph all[\"전체\"]\n    a[A] --> b[B]\n  end\n")
    assert graph.groups == {}
    two = ds.parse("flowchart LR\n  subgraph x[\"X\"]\n    a[A]\n  end\n  b[B]\n  a --> b\n")
    assert list(two.groups) == ["x"]  # a zone that frames only some of the nodes stays
