"""The store: one shared catalogue, and copies taken from it.

The agents and skills a workspace ships with used to be written into every
account at approval — the same procedure as N rows, where improving one reached
nobody. They live in the administrator's account now, shared, and everybody
else takes a copy of the ones they want. These tests are about the copy: that
it is a copy and not a reference, that taking it twice is not two rows, and
that an agent arrives able to run the skills its card advertised.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList

from app.models.user import User, UserRole
from app.models.workspace import Agent, Skill, SkillSource, StoredFile, Visibility
from app.routers import workspace as ws

# ── an in-memory database that reads the query it is given ──────────────
#
# Tables here are lists, and the router's `where` clauses are walked rather
# than recognised. A fake that answers by call order passes for exactly as long
# as the routes ask their questions in the order they were written in.


def _bound(element):
    """The Python value behind a bind parameter, list included for `IN`."""
    return getattr(element, "value", element)


def _matches(row, clause) -> bool:
    if clause is None:
        return True
    if isinstance(clause, BooleanClauseList):
        results = [_matches(row, part) for part in clause.clauses]
        return all(results) if clause.operator.__name__ == "and_" else any(results)
    assert isinstance(clause, BinaryExpression), f"unsupported clause: {clause}"
    name = clause.left.name
    actual = getattr(row, name)
    expected = _bound(clause.right)
    op = clause.operator.__name__
    if op == "in_op":
        return actual in list(expected or [])
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    raise AssertionError(f"unsupported operator: {op}")


class _Result:
    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """Skills, agents, users and files, queried the way the routes query them."""

    def __init__(self, *, skills=None, agents=None, users=None, files=None):
        self.tables = {
            "skills": list(skills or []),
            "agents": list(agents or []),
            "users": list(users or []),
            "files": list(files or []),
        }
        self.commits = 0

    async def get(self, model, row_id):
        table = model.__tablename__
        return next((r for r in self.tables[table] if r.id == row_id), None)

    async def exec(self, query):
        table = query.get_final_froms()[0].name
        rows = [r for r in self.tables[table] if _matches(r, query.whereclause)]
        # `select(User.id)` and `select(StoredFile.agent_id)` want the column,
        # not the row; `select(Skill)` selects every column and wants the row.
        selected = list(query.selected_columns.keys())
        if len(selected) == 1:
            return _Result([getattr(r, selected[0]) for r in rows])
        return _Result(rows)

    def add(self, row):
        table = row.__tablename__
        if all(existing is not row for existing in self.tables[table]):
            self.tables[table].append(row)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, _row):
        pass

    @property
    def skills(self) -> list[Skill]:
        return self.tables["skills"]

    @property
    def agents(self) -> list[Agent]:
        return self.tables["agents"]


def _user(user_id="user-1", role=UserRole.user) -> User:
    return User(
        id=user_id, email=f"{user_id}@x", password_hash="x", name=user_id, role=role
    )


def _skill(**kwargs) -> Skill:
    return Skill(
        **{
            "id": "skill-1",
            "owner_id": "admin-1",
            "name": "인용 형식 맞추기",
            "slug": "인용-형식-맞추기",
            "description": "설명",
            "when_to_use": "제출용 글을 쓸 때",
            "body": "절차",
            "catalog_key": "citation",
            "source": SkillSource.built_in,
            "kinds": ["chat"],
            "required_tools": [],
            "estimated_tokens": 40,
            "visibility": Visibility.org,
            **kwargs,
        }
    )


def _agent(**kwargs) -> Agent:
    return Agent(
        **{
            "id": "agent-1",
            "owner_id": "admin-1",
            "name": "논문 리뷰어",
            "slug": "논문-리뷰어",
            "description": "설명",
            "system_prompt": "프롬프트",
            "kinds": ["chat"],
            "tools": ["fetch_url"],
            "skill_ids": ["skill-1"],
            "visibility": Visibility.org,
            "catalog_key": "paper-reviewer",
            **kwargs,
        }
    )


# ── the store ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_store_lists_other_peoples_shared_skills_and_says_who_published():
    admin = _user("admin-1", UserRole.admin)
    colleague = _user("user-2")
    shared = _skill()
    theirs = _skill(
        id="skill-2", owner_id="user-2", name="개인 절차", slug="개인-절차", catalog_key=None
    )
    mine = _skill(
        id="skill-3",
        owner_id="user-1",
        slug="내-절차",
        catalog_key=None,
        visibility=Visibility.private,
    )
    db = _Db(skills=[shared, theirs, mine], users=[admin, colleague])

    rows = await ws.list_skill_store(_user(), db)

    # Your own rows are not in the store, shared or not.
    assert {r.id for r in rows} == {"skill-1", "skill-2"}
    official = next(r for r in rows if r.id == "skill-1")
    assert official.official and official.owner_name == "admin-1"
    assert not next(r for r in rows if r.id == "skill-2").official
    assert not any(r.installed for r in rows)


@pytest.mark.asyncio
async def test_a_skill_already_copied_is_marked_installed_rather_than_offered_again():
    shared = _skill()
    copy = _skill(
        id="skill-9", owner_id="user-1", visibility=Visibility.private, origin_id="skill-1"
    )
    db = _Db(skills=[shared, copy], users=[_user("admin-1", UserRole.admin)])

    rows = await ws.list_skill_store(_user(), db)

    assert [r.installed for r in rows] == [True]


@pytest.mark.asyncio
async def test_an_account_seeded_before_the_catalog_existed_is_not_offered_its_own_rows():
    """Every account used to hold its own copy of all of this.

    Those rows carry no origin — nothing was copied — so without matching the
    catalogue key the store would tell an existing account it holds none of the
    procedures it has been using for months.
    """
    shared = _skill()
    seeded = _skill(id="skill-9", owner_id="user-1", visibility=Visibility.private)
    db = _Db(skills=[shared, seeded], users=[_user("admin-1", UserRole.admin)])

    rows = await ws.list_skill_store(_user(), db)

    assert rows[0].installed


# ── taking a copy ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_installing_a_skill_copies_it_privately_and_counts_the_install():
    shared = _skill()
    db = _Db(skills=[shared])

    out = await ws.install_skill("skill-1", _user(), db)

    copy = next(s for s in db.skills if s.id == out.id)
    assert copy is not shared
    assert copy.owner_id == "user-1"
    assert copy.body == shared.body
    assert copy.origin_id == "skill-1"
    # The store lists originals. A copy that published itself would fill the
    # store with copies of its own entries.
    assert copy.visibility is Visibility.private
    assert shared.installs == 1
    assert db.commits == 1


@pytest.mark.asyncio
async def test_installing_the_same_skill_twice_is_the_same_row():
    shared = _skill()
    db = _Db(skills=[shared])

    first = await ws.install_skill("skill-1", _user(), db)
    second = await ws.install_skill("skill-1", _user(), db)

    assert first.id == second.id
    assert len(db.skills) == 2
    assert shared.installs == 1


@pytest.mark.asyncio
async def test_a_colleagues_skill_arrives_as_a_workspace_one_not_as_your_own_writing():
    theirs = _skill(owner_id="user-2", source=SkillSource.personal, catalog_key=None)
    db = _Db(skills=[theirs])

    out = await ws.install_skill("skill-1", _user(), db)

    assert out.source is SkillSource.workspace


@pytest.mark.asyncio
async def test_a_private_skill_cannot_be_installed_and_your_own_is_refused():
    private = _skill(visibility=Visibility.private)
    db = _Db(skills=[private])
    with pytest.raises(HTTPException) as unshared:
        await ws.install_skill("skill-1", _user(), db)
    assert unshared.value.status_code == 404

    db = _Db(skills=[_skill(owner_id="user-1")])
    with pytest.raises(HTTPException) as own:
        await ws.install_skill("skill-1", _user(), db)
    assert own.value.status_code == 409


@pytest.mark.asyncio
async def test_installing_an_agent_brings_the_skills_it_runs_on():
    """The prompt used to travel alone.

    An allow-list of skill ids is a list of rows in the author's account. Copied
    verbatim it resolves to nothing here, and the imported agent answers
    differently from the one on the card without saying why.
    """
    shared_skill = _skill()
    db = _Db(skills=[shared_skill], agents=[_agent()])

    out = await ws.install_agent("agent-1", _user(), db)

    copy = next(a for a in db.agents if a.id == out.id)
    assert copy.owner_id == "user-1"
    assert copy.system_prompt == "프롬프트"
    assert copy.visibility is Visibility.private
    assert copy.origin_id == "agent-1"
    installed = next(s for s in db.skills if s.owner_id == "user-1")
    assert copy.skill_ids == [installed.id]
    assert installed.origin_id == "skill-1"
    assert shared_skill.installs == 1


@pytest.mark.asyncio
async def test_an_allow_list_emptied_by_the_copy_inherits_rather_than_denies():
    """`[]` is not "none survived" — it is a hard deny.

    An agent copied with an emptied allow-list would refuse every skill its new
    owner ever switched on, silently.
    """
    unshared = _skill(visibility=Visibility.private)
    db = _Db(skills=[unshared], agents=[_agent()])

    out = await ws.install_agent("agent-1", _user(), db)

    assert out.skill_ids is None
    assert not any(s.owner_id == "user-1" for s in db.skills)


@pytest.mark.asyncio
async def test_an_agent_that_denies_every_skill_keeps_denying_them():
    db = _Db(skills=[], agents=[_agent(skill_ids=[])])
    out = await ws.install_agent("agent-1", _user(), db)
    assert out.skill_ids == []


@pytest.mark.asyncio
async def test_installing_an_agent_twice_is_the_same_row():
    db = _Db(skills=[_skill()], agents=[_agent()])

    first = await ws.install_agent("agent-1", _user(), db)
    second = await ws.install_agent("agent-1", _user(), db)

    assert first.id == second.id
    assert len(db.agents) == 2


@pytest.mark.asyncio
async def test_a_knowledge_shelf_does_not_travel_and_the_copy_says_so():
    """Copying an agent is not a grant over the author's documents.

    Said on the copy, because the copy is where the question gets asked: an
    agent that searched its author's papers and now searches nothing looks
    identical to one that never had any.
    """
    shelf = StoredFile(
        id="file-1", user_id="admin-1", agent_id="agent-1", name="x.pdf", text="내용"
    )
    db = _Db(skills=[_skill()], agents=[_agent()], files=[shelf])

    out = await ws.install_agent("agent-1", _user(), db)

    assert not out.has_knowledge
    assert shelf.user_id == "admin-1"
    assert "지식 문서는 원본 소유자의 것이라 함께 오지 않습니다" in out.description


@pytest.mark.asyncio
async def test_an_agent_with_no_shelf_is_copied_without_an_apology_for_one():
    db = _Db(skills=[_skill()], agents=[_agent()])
    out = await ws.install_agent("agent-1", _user(), db)
    assert out.description == "설명"


@pytest.mark.asyncio
async def test_the_agent_list_marks_official_entries_and_the_ones_already_taken():
    admin = _user("admin-1", UserRole.admin)
    shared = _agent()
    copy = _agent(
        id="agent-9", owner_id="user-1", visibility=Visibility.private, origin_id="agent-1"
    )
    db = _Db(agents=[shared, copy], users=[admin, _user()])

    rows = await ws.list_agents(_user(), db)

    original = next(r for r in rows if r.id == "agent-1")
    assert original.official and original.installed
    # Your own row never claims you hold a copy of yourself.
    assert not next(r for r in rows if r.id == "agent-9").installed


@pytest.mark.asyncio
async def test_the_copy_every_account_was_handed_before_the_store_hides_the_catalogue_row():
    """옛 사본 때문에 기본 에이전트가 두 번 보이던 것.

    Before the shipped agents became one shared catalogue, every account was
    handed its own copy of each. The move made the originals org-owned and left
    the copies where they were, so an account that existed before it saw every
    built-in twice — 리포트 도우미 above 리포트 도우미, one of them with a
    different sentence under it. A person opening KloudChat on such an account
    reads that as two different agents and cannot tell which to press.

    The catalogue row is the one that goes, because which of the two is
    untouched cannot be known: the migration rewrote both, so their timestamps
    say a day passed and mean nothing. Between a row somebody may have edited
    and a row they can take from the store again whenever they like, the safe
    one to hide is the one that comes back.
    """
    admin = _user("admin-1", UserRole.admin)
    shared = _agent()
    #: No `origin_id` — nobody installed this, it was seeded.
    seeded = _agent(id="agent-9", owner_id="user-1", visibility=Visibility.private)
    db = _Db(agents=[shared, seeded], users=[admin, _user()])

    rows = await ws.list_agents(_user(), db)

    assert [row.id for row in rows] == ["agent-9"]


@pytest.mark.asyncio
async def test_an_account_with_no_copy_of_its_own_still_sees_the_catalogue():
    """Which is every account made since. Nothing about the fix reaches them."""
    admin = _user("admin-1", UserRole.admin)
    db = _Db(agents=[_agent()], users=[admin, _user()])

    rows = await ws.list_agents(_user(), db)

    assert [row.id for row in rows] == ["agent-1"]
