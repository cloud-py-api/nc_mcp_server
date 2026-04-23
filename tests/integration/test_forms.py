"""Integration tests for Forms tools against a real Nextcloud instance."""

import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


async def _make_form(nc_mcp: McpTestHelper, title: str, **extra: Any) -> dict[str, Any]:
    """Create a form and set its title (and optional other fields). Returns the form dict."""
    created: dict[str, Any] = json.loads(await nc_mcp.call("create_form"))
    kv: dict[str, Any] = {"title": title}
    kv.update(extra)
    updated: dict[str, Any] = json.loads(await nc_mcp.call("update_form", form_id=created["id"], key_value_pairs=kv))
    return updated


class TestListForms:
    @pytest.mark.asyncio
    async def test_list_returns_created_form(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-list-owned")
        forms = json.loads(await nc_mcp.call("list_forms", ownership="owned"))
        titles = [f["title"] for f in forms]
        assert "mcp-test-list-owned" in titles
        assert any(f["id"] == form["id"] for f in forms)

    @pytest.mark.asyncio
    async def test_list_without_type_returns_list(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-list-all")
        forms: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_forms"))
        assert isinstance(forms, list)
        assert any(f["id"] == form["id"] for f in forms), "owned form should appear in default (merged) list"

    @pytest.mark.asyncio
    async def test_list_default_is_deduped(self, nc_mcp: McpTestHelper) -> None:
        """The default merged list fans out to both owned+shared; a form must appear once."""
        form = await _make_form(nc_mcp, "mcp-test-list-dedup")
        forms: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_forms"))
        matches = [f for f in forms if f["id"] == form["id"]]
        assert len(matches) == 1, "form id appeared more than once in merged list"


class TestFormLifecycle:
    @pytest.mark.asyncio
    async def test_create_produces_form_with_id(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_form"))
        assert isinstance(created["id"], int)
        assert created["ownerId"] == "admin"
        assert created["hash"]

    @pytest.mark.asyncio
    async def test_update_persists_changes(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_form"))
        kv = {"title": "mcp-test-update", "description": "hello"}
        updated = json.loads(await nc_mcp.call("update_form", form_id=created["id"], key_value_pairs=kv))
        assert updated["title"] == "mcp-test-update"
        assert updated["description"] == "hello"
        fetched = json.loads(await nc_mcp.call("get_form", form_id=created["id"]))
        assert fetched["title"] == "mcp-test-update"
        assert fetched["description"] == "hello"

    @pytest.mark.asyncio
    async def test_create_from_existing_form_copies_structure(self, nc_mcp: McpTestHelper) -> None:
        original = await _make_form(nc_mcp, "mcp-test-clone-source")
        await nc_mcp.call("create_question", form_id=original["id"], question_type="short", text="What's your name?")
        clone = json.loads(await nc_mcp.call("create_form", from_id=original["id"]))
        assert clone["id"] != original["id"]
        clone_full = json.loads(await nc_mcp.call("get_form", form_id=clone["id"]))
        assert len(clone_full["questions"]) == 1
        assert clone_full["questions"][0]["text"] == "What's your name?"

    @pytest.mark.asyncio
    async def test_delete_removes_form(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-delete")
        result = json.loads(await nc_mcp.call("delete_form", form_id=form["id"]))
        assert result == {"deleted_form_id": form["id"]}
        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("get_form", form_id=form["id"])

    @pytest.mark.asyncio
    async def test_get_nonexistent_form_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("get_form", form_id=999_999_999)


class TestQuestions:
    @pytest.mark.asyncio
    async def test_create_question_with_text(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-q-create")
        q = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="short", text="Name?"))
        assert q["type"] == "short"
        assert q["text"] == "Name?"
        assert q["formId"] == form["id"]

    @pytest.mark.asyncio
    async def test_create_question_rejects_bad_type(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-q-bad")
        with pytest.raises((ToolError, ValueError), match=r"[Ii]nvalid question_type"):
            await nc_mcp.call("create_question", form_id=form["id"], question_type="invalid_type", text="x")

    @pytest.mark.asyncio
    async def test_create_question_rejects_datetime(self, nc_mcp: McpTestHelper) -> None:
        """Nextcloud no longer supports 'datetime' — our validation rejects it client-side."""
        form = await _make_form(nc_mcp, "mcp-test-q-datetime")
        with pytest.raises((ToolError, ValueError), match=r"[Ii]nvalid question_type"):
            await nc_mcp.call("create_question", form_id=form["id"], question_type="datetime", text="x")

    @pytest.mark.asyncio
    async def test_create_question_linearscale(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-q-linearscale")
        q = json.loads(
            await nc_mcp.call("create_question", form_id=form["id"], question_type="linearscale", text="Rate")
        )
        assert q["type"] == "linearscale"

    @pytest.mark.asyncio
    async def test_create_question_color(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-q-color")
        q = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="color", text="Favorite"))
        assert q["type"] == "color"

    @pytest.mark.asyncio
    async def test_list_questions_includes_created(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-q-list")
        q = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="long", text="Comments?"))
        questions = json.loads(await nc_mcp.call("list_questions", form_id=form["id"]))
        assert any(qq["id"] == q["id"] for qq in questions)

    @pytest.mark.asyncio
    async def test_get_question_returns_single(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-q-get")
        q = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="short", text="X"))
        fetched = json.loads(await nc_mcp.call("get_question", form_id=form["id"], question_id=q["id"]))
        assert fetched["id"] == q["id"]
        assert fetched["text"] == "X"

    @pytest.mark.asyncio
    async def test_update_question_changes_text(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-q-update")
        q = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="short", text="old"))
        await nc_mcp.call(
            "update_question",
            form_id=form["id"],
            question_id=q["id"],
            key_value_pairs={"text": "new", "isRequired": True},
        )
        fetched = json.loads(await nc_mcp.call("get_question", form_id=form["id"], question_id=q["id"]))
        assert fetched["text"] == "new"
        assert fetched["isRequired"] is True

    @pytest.mark.asyncio
    async def test_reorder_questions(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-q-reorder")
        q1 = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="short", text="q1"))
        q2 = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="short", text="q2"))
        await nc_mcp.call("reorder_questions", form_id=form["id"], new_order=[q2["id"], q1["id"]])
        full = json.loads(await nc_mcp.call("get_form", form_id=form["id"]))
        # Order field determines the order; sort by it.
        ordered = sorted(full["questions"], key=lambda q: q["order"])
        assert ordered[0]["id"] == q2["id"]
        assert ordered[1]["id"] == q1["id"]

    @pytest.mark.asyncio
    async def test_delete_question(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-q-delete")
        q = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="short", text="x"))
        result = json.loads(await nc_mcp.call("delete_question", form_id=form["id"], question_id=q["id"]))
        assert result == {"deleted_question_id": q["id"]}
        full = json.loads(await nc_mcp.call("get_form", form_id=form["id"]))
        assert not any(qq["id"] == q["id"] for qq in full["questions"])


class TestOptions:
    @pytest.mark.asyncio
    async def test_create_options_for_choice_question(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-opt-create")
        q = json.loads(
            await nc_mcp.call("create_question", form_id=form["id"], question_type="multiple_unique", text="Pick one")
        )
        options = json.loads(
            await nc_mcp.call(
                "create_options",
                form_id=form["id"],
                question_id=q["id"],
                option_texts=["Yes", "No", "Maybe"],
            )
        )
        assert len(options) == 3
        assert [o["text"] for o in options] == ["Yes", "No", "Maybe"]

    @pytest.mark.asyncio
    async def test_update_option_text(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-opt-update")
        q = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="dropdown", text="Pick"))
        opts = json.loads(
            await nc_mcp.call("create_options", form_id=form["id"], question_id=q["id"], option_texts=["a"])
        )
        await nc_mcp.call(
            "update_option",
            form_id=form["id"],
            question_id=q["id"],
            option_id=opts[0]["id"],
            key_value_pairs={"text": "alpha"},
        )
        fetched = json.loads(await nc_mcp.call("get_question", form_id=form["id"], question_id=q["id"]))
        assert fetched["options"][0]["text"] == "alpha"

    @pytest.mark.asyncio
    async def test_reorder_options(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-opt-reorder")
        q = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="dropdown", text="Pick"))
        opts = json.loads(
            await nc_mcp.call(
                "create_options",
                form_id=form["id"],
                question_id=q["id"],
                option_texts=["a", "b", "c"],
            )
        )
        new_order = [opts[2]["id"], opts[0]["id"], opts[1]["id"]]
        await nc_mcp.call("reorder_options", form_id=form["id"], question_id=q["id"], new_order=new_order)
        fetched = json.loads(await nc_mcp.call("get_question", form_id=form["id"], question_id=q["id"]))
        ordered = sorted(fetched["options"], key=lambda o: o["order"])
        assert [o["id"] for o in ordered] == new_order

    @pytest.mark.asyncio
    async def test_delete_option(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-opt-delete")
        q = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="dropdown", text="Pick"))
        opts = json.loads(
            await nc_mcp.call(
                "create_options",
                form_id=form["id"],
                question_id=q["id"],
                option_texts=["keep", "remove"],
            )
        )
        target = opts[1]["id"]
        result = json.loads(
            await nc_mcp.call(
                "delete_option",
                form_id=form["id"],
                question_id=q["id"],
                option_id=target,
            )
        )
        assert result == {"deleted_option_id": target}
        fetched = json.loads(await nc_mcp.call("get_question", form_id=form["id"], question_id=q["id"]))
        assert not any(o["id"] == target for o in fetched["options"])


class TestShares:
    @pytest.mark.asyncio
    async def test_create_link_share(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-share-link")
        share = json.loads(
            await nc_mcp.call(
                "create_form_share",
                form_id=form["id"],
                share_type=3,  # link
                permissions=["submit"],
            )
        )
        assert share["shareType"] == 3
        assert "submit" in share["permissions"]

    @pytest.mark.asyncio
    async def test_update_share_permissions(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-share-update")
        share = json.loads(
            await nc_mcp.call("create_form_share", form_id=form["id"], share_type=3, permissions=["submit"])
        )
        await nc_mcp.call(
            "update_form_share",
            form_id=form["id"],
            share_id=share["id"],
            key_value_pairs={"permissions": ["submit", "embed"]},
        )
        full = json.loads(await nc_mcp.call("get_form", form_id=form["id"]))
        match = next(s for s in full["shares"] if s["id"] == share["id"])
        assert "embed" in match["permissions"]

    @pytest.mark.asyncio
    async def test_delete_share(self, nc_mcp: McpTestHelper) -> None:
        form = await _make_form(nc_mcp, "mcp-test-share-delete")
        share = json.loads(
            await nc_mcp.call("create_form_share", form_id=form["id"], share_type=3, permissions=["submit"])
        )
        result = json.loads(await nc_mcp.call("delete_form_share", form_id=form["id"], share_id=share["id"]))
        assert result == {"deleted_share_id": share["id"]}
        full = json.loads(await nc_mcp.call("get_form", form_id=form["id"]))
        assert not any(s["id"] == share["id"] for s in full["shares"])


class TestSubmissions:
    async def _build_submittable_form(self, nc_mcp: McpTestHelper, title: str) -> tuple[int, int]:
        """Create a form with access permitAllUsers, add one short question. Returns (form_id, question_id)."""
        form = await _make_form(
            nc_mcp,
            title,
            access={"permitAllUsers": True, "showToAllUsers": True},
        )
        q = json.loads(await nc_mcp.call("create_question", form_id=form["id"], question_type="short", text="Answer"))
        return form["id"], q["id"]

    @pytest.mark.asyncio
    async def test_submit_and_list(self, nc_mcp: McpTestHelper) -> None:
        form_id, q_id = await self._build_submittable_form(nc_mcp, "mcp-test-submit")
        await nc_mcp.call("submit_form", form_id=form_id, answers={str(q_id): ["hello world"]})
        listing = json.loads(await nc_mcp.call("list_submissions", form_id=form_id))
        assert listing["filteredSubmissionsCount"] == 1
        assert len(listing["submissions"]) == 1
        answers = listing["submissions"][0]["answers"]
        assert any(a["text"] == "hello world" for a in answers)

    @pytest.mark.asyncio
    async def test_get_submission(self, nc_mcp: McpTestHelper) -> None:
        form_id, q_id = await self._build_submittable_form(nc_mcp, "mcp-test-sub-get")
        await nc_mcp.call("submit_form", form_id=form_id, answers={str(q_id): ["abc"]})
        listing = json.loads(await nc_mcp.call("list_submissions", form_id=form_id))
        sid = listing["submissions"][0]["id"]
        single = json.loads(await nc_mcp.call("get_submission", form_id=form_id, submission_id=sid))
        assert single["id"] == sid
        assert any(a["text"] == "abc" for a in single["answers"])

    @pytest.mark.asyncio
    async def test_delete_single_submission(self, nc_mcp: McpTestHelper) -> None:
        form_id, q_id = await self._build_submittable_form(nc_mcp, "mcp-test-sub-del")
        await nc_mcp.call("update_form", form_id=form_id, key_value_pairs={"submitMultiple": True})
        await nc_mcp.call("submit_form", form_id=form_id, answers={str(q_id): ["one"]})
        await nc_mcp.call("submit_form", form_id=form_id, answers={str(q_id): ["two"]})
        listing = json.loads(await nc_mcp.call("list_submissions", form_id=form_id))
        assert listing["filteredSubmissionsCount"] == 2
        first_id = listing["submissions"][0]["id"]
        result = json.loads(await nc_mcp.call("delete_submission", form_id=form_id, submission_id=first_id))
        assert result == {"deleted_submission_id": first_id}
        after = json.loads(await nc_mcp.call("list_submissions", form_id=form_id))
        assert after["filteredSubmissionsCount"] == 1

    @pytest.mark.asyncio
    async def test_delete_all_submissions(self, nc_mcp: McpTestHelper) -> None:
        form_id, q_id = await self._build_submittable_form(nc_mcp, "mcp-test-sub-del-all")
        await nc_mcp.call("update_form", form_id=form_id, key_value_pairs={"submitMultiple": True})
        await nc_mcp.call("submit_form", form_id=form_id, answers={str(q_id): ["a"]})
        await nc_mcp.call("submit_form", form_id=form_id, answers={str(q_id): ["b"]})
        result = json.loads(await nc_mcp.call("delete_all_submissions", form_id=form_id))
        assert result == {"cleared_form_id": form_id}
        after = json.loads(await nc_mcp.call("list_submissions", form_id=form_id))
        assert after["filteredSubmissionsCount"] == 0


class TestFormsPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_forms")
        assert isinstance(json.loads(result), list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("create_form")

    @pytest.mark.asyncio
    async def test_read_only_blocks_delete(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("delete_form", form_id=1)

    @pytest.mark.asyncio
    async def test_write_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("delete_form", form_id=1)

    @pytest.mark.asyncio
    async def test_write_allows_create_and_update(self, nc_mcp_write: McpTestHelper) -> None:
        created = json.loads(await nc_mcp_write.call("create_form"))
        updated = json.loads(
            await nc_mcp_write.call(
                "update_form",
                form_id=created["id"],
                key_value_pairs={"title": "mcp-test-perm-write"},
            )
        )
        assert updated["title"] == "mcp-test-perm-write"
