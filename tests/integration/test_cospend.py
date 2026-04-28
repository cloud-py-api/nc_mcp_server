"""Integration tests for Cospend tools against a real Nextcloud instance."""

import json
from typing import Any, ClassVar

import niquests
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.config import Config

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def _cospend_available(_cleanup_config: Config) -> bool:
    """Probe whether the Cospend app is enabled on the target Nextcloud."""
    try:
        resp = niquests.get(
            f"{_cleanup_config.nextcloud_url}/ocs/v2.php/apps/cospend/api/v1/ping",
            auth=(_cleanup_config.user, _cleanup_config.password),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=5,
        )
    except (OSError, niquests.exceptions.RequestException):
        return False
    return resp.ok


@pytest.fixture(autouse=True)
def _skip_if_no_cospend(_cospend_available: bool) -> None:
    if not _cospend_available:
        pytest.skip("Cospend app is not enabled on this Nextcloud instance")


async def _make_project(nc_mcp: McpTestHelper, project_id: str, name: str | None = None) -> dict[str, Any]:
    return json.loads(await nc_mcp.call("create_cospend_project", project_id=project_id, name=name or project_id))


async def _make_member(nc_mcp: McpTestHelper, project_id: str, name: str, **extra: Any) -> dict[str, Any]:
    return json.loads(await nc_mcp.call("create_cospend_member", project_id=project_id, name=name, **extra))


async def _make_bill(
    nc_mcp: McpTestHelper,
    project_id: str,
    what: str,
    amount: float,
    payer: int,
    payed_for: list[int],
    **extra: Any,
) -> int:
    result = json.loads(
        await nc_mcp.call(
            "create_cospend_bill",
            project_id=project_id,
            what=what,
            amount=amount,
            payer=payer,
            payed_for=payed_for,
            **extra,
        )
    )
    return int(result["bill_id"])


class TestProjectLifecycle:
    @pytest.mark.asyncio
    async def test_create_returns_full_project(self, nc_mcp: McpTestHelper) -> None:
        project = await _make_project(nc_mcp, "mcp-test-create", "Create Test")
        assert project["id"] == "mcp-test-create"
        assert project["name"] == "Create Test"
        assert isinstance(project["categories"], dict)
        assert project["categories"]
        assert isinstance(project["paymentmodes"], dict)
        assert project["paymentmodes"]

    @pytest.mark.asyncio
    async def test_create_with_taken_id_gets_suffix(self, nc_mcp: McpTestHelper) -> None:
        first = await _make_project(nc_mcp, "mcp-test-dup", "First")
        second = await _make_project(nc_mcp, "mcp-test-dup", "Second")
        assert first["id"] == "mcp-test-dup"
        assert second["id"] != first["id"]
        assert second["id"].startswith("mcp-test-dup")

    @pytest.mark.asyncio
    async def test_list_includes_created(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-list-a")
        await _make_project(nc_mcp, "mcp-test-list-b")
        projects = json.loads(await nc_mcp.call("list_cospend_projects"))
        ids = [p["id"] for p in projects]
        assert "mcp-test-list-a" in ids
        assert "mcp-test-list-b" in ids

    @pytest.mark.asyncio
    async def test_get_returns_project(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-get", "Get Me")
        project = json.loads(await nc_mcp.call("get_cospend_project", project_id="mcp-test-get"))
        assert project["id"] == "mcp-test-get"
        assert project["name"] == "Get Me"

    @pytest.mark.asyncio
    async def test_update_changes_name(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-update")
        result = json.loads(await nc_mcp.call("update_cospend_project", project_id="mcp-test-update", name="Renamed"))
        assert result == {"project_id": "mcp-test-update", "updated": True}
        fetched = json.loads(await nc_mcp.call("get_cospend_project", project_id="mcp-test-update"))
        assert fetched["name"] == "Renamed"

    @pytest.mark.asyncio
    async def test_update_archived_ts_is_persisted(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-archive")
        await nc_mcp.call(
            "update_cospend_project",
            project_id="mcp-test-archive",
            archived_ts=1_700_000_000,
        )
        fetched = json.loads(await nc_mcp.call("get_cospend_project", project_id="mcp-test-archive"))
        assert fetched["archived_ts"] == 1_700_000_000

    @pytest.mark.asyncio
    async def test_archived_ts_special_values(self, nc_mcp: McpTestHelper) -> None:
        """archived_ts=0 archives NOW (Cospend's ARCHIVED_TS_NOW), -1 unarchives (ARCHIVED_TS_UNSET).

        Documents the inverted-feeling sentinel values so we don't regress the docstring.
        """
        pid = "mcp-test-archive-special"
        await _make_project(nc_mcp, pid)

        # archived_ts=0 → archives at "now" (server records current Unix ts, not zero)
        await nc_mcp.call("update_cospend_project", project_id=pid, archived_ts=0)
        fetched = json.loads(await nc_mcp.call("get_cospend_project", project_id=pid))
        archived = fetched["archived_ts"]
        assert isinstance(archived, int), "archived_ts=0 must produce an int (current ts), not unarchive"
        assert archived > 0, "archived_ts=0 must record a positive current Unix timestamp"

        # archived_ts=-1 → unarchives (clears the field to None)
        await nc_mcp.call("update_cospend_project", project_id=pid, archived_ts=-1)
        fetched = json.loads(await nc_mcp.call("get_cospend_project", project_id=pid))
        assert fetched["archived_ts"] is None, "archived_ts=-1 should unarchive (clear the field)"

    @pytest.mark.asyncio
    async def test_delete_removes_project(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-del")
        result = json.loads(await nc_mcp.call("delete_cospend_project", project_id="mcp-test-del"))
        assert result["project_id"] == "mcp-test-del"
        assert result["message"] == "DELETED"
        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("get_cospend_project", project_id="mcp-test-del")

    @pytest.mark.asyncio
    async def test_get_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("get_cospend_project", project_id="mcp-test-does-not-exist")

    @pytest.mark.asyncio
    async def test_project_id_with_space_round_trips(self, nc_mcp: McpTestHelper) -> None:
        """Cospend allows spaces in project ids — verify URL-encoding so subsequent ops don't 404."""
        pid = "mcp-test with space"
        created = await _make_project(nc_mcp, pid, "Spaced")
        assert created["id"] == pid
        fetched = json.loads(await nc_mcp.call("get_cospend_project", project_id=pid))
        assert fetched["id"] == pid
        member = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        bill_id = await _make_bill(nc_mcp, pid, "Pizza", 10.0, member["id"], [member["id"], bob["id"]])
        bill = json.loads(await nc_mcp.call("get_cospend_bill", project_id=pid, bill_id=bill_id))
        assert bill["amount"] == 10.0
        await nc_mcp.call("delete_cospend_project", project_id=pid)


class TestMembers:
    @pytest.mark.asyncio
    async def test_create_returns_member(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-mem-create")
        member = await _make_member(nc_mcp, "mcp-test-mem-create", "Alice", weight=2.0)
        assert member["name"] == "Alice"
        assert member["weight"] == 2.0
        assert member["activated"] is True
        assert isinstance(member["id"], int)

    @pytest.mark.asyncio
    async def test_list_returns_created_members(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-mem-list")
        await _make_member(nc_mcp, "mcp-test-mem-list", "Alice")
        await _make_member(nc_mcp, "mcp-test-mem-list", "Bob")
        members = json.loads(await nc_mcp.call("list_cospend_members", project_id="mcp-test-mem-list"))
        assert {m["name"] for m in members} == {"Alice", "Bob"}

    @pytest.mark.asyncio
    async def test_create_with_color_persists_color(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-mem-color")
        member = await _make_member(nc_mcp, "mcp-test-mem-color", "Charlie", color="#1a2b3c")
        assert member["color"] == {"r": 0x1A, "g": 0x2B, "b": 0x3C}

    @pytest.mark.asyncio
    async def test_update_renames_member(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-mem-rename")
        member = await _make_member(nc_mcp, "mcp-test-mem-rename", "Old")
        updated = json.loads(
            await nc_mcp.call(
                "update_cospend_member",
                project_id="mcp-test-mem-rename",
                member_id=member["id"],
                name="New",
            )
        )
        assert updated["name"] == "New"

    @pytest.mark.asyncio
    async def test_update_weight_persists(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-mem-weight")
        member = await _make_member(nc_mcp, "mcp-test-mem-weight", "Heavy")
        updated = json.loads(
            await nc_mcp.call(
                "update_cospend_member",
                project_id="mcp-test-mem-weight",
                member_id=member["id"],
                weight=3.5,
            )
        )
        assert updated["weight"] == 3.5

    @pytest.mark.asyncio
    async def test_update_activated_false_then_true_round_trip(self, nc_mcp: McpTestHelper) -> None:
        """Soft-disable via update path, then re-enable. Catches the create/update field-name asymmetry
        (create uses `active` int, update uses `activated` bool)."""
        pid = "mcp-test-mem-toggle"
        await _make_project(nc_mcp, pid)
        member = await _make_member(nc_mcp, pid, "Toggle")
        bob = await _make_member(nc_mcp, pid, "Bob")
        # Member must own a bill so update(activated=False) soft-disables instead of deleting
        await _make_bill(nc_mcp, pid, "Lunch", 10.0, payer=member["id"], payed_for=[member["id"], bob["id"]])

        disabled = json.loads(
            await nc_mcp.call(
                "update_cospend_member",
                project_id=pid,
                member_id=member["id"],
                activated=False,
            )
        )
        assert disabled is not None, "member with bills should be returned as soft-disabled, not deleted"
        assert disabled["activated"] is False
        members = json.loads(await nc_mcp.call("list_cospend_members", project_id=pid))
        assert next(m for m in members if m["id"] == member["id"])["activated"] is False

        re_enabled = json.loads(
            await nc_mcp.call(
                "update_cospend_member",
                project_id=pid,
                member_id=member["id"],
                activated=True,
            )
        )
        assert re_enabled["activated"] is True

    @pytest.mark.asyncio
    async def test_delete_removes_member_with_no_bills(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-mem-del")
        member = await _make_member(nc_mcp, "mcp-test-mem-del", "Doomed")
        result = json.loads(
            await nc_mcp.call(
                "delete_cospend_member",
                project_id="mcp-test-mem-del",
                member_id=member["id"],
            )
        )
        assert result == {"project_id": "mcp-test-mem-del", "member_id": member["id"], "deleted": True}
        members = json.loads(await nc_mcp.call("list_cospend_members", project_id="mcp-test-mem-del"))
        assert all(m["id"] != member["id"] for m in members)

    @pytest.mark.asyncio
    async def test_delete_member_with_bills_disables_them(self, nc_mcp: McpTestHelper) -> None:
        """Members owning a bill cannot be hard-deleted; the API soft-disables them."""
        pid = "mcp-test-mem-disable"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        await _make_bill(nc_mcp, pid, "Pizza", 10.0, payer=alice["id"], payed_for=[alice["id"], bob["id"]])
        await nc_mcp.call("delete_cospend_member", project_id=pid, member_id=alice["id"])
        members = json.loads(await nc_mcp.call("list_cospend_members", project_id=pid))
        # alice is still in the list but disabled, so bill history stays valid
        alice_after = next((m for m in members if m["id"] == alice["id"]), None)
        assert alice_after is not None, "member with bills should be kept (soft-disabled), not removed"
        assert alice_after["activated"] is False


class TestBills:
    @pytest.mark.asyncio
    async def test_create_returns_bill_id(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-bill-create"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        bill_id = await _make_bill(nc_mcp, pid, "Pizza", 20.0, payer=alice["id"], payed_for=[alice["id"], bob["id"]])
        assert isinstance(bill_id, int)
        assert bill_id > 0

    @pytest.mark.asyncio
    async def test_get_returns_bill_fields(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-bill-get"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        bill_id = await _make_bill(
            nc_mcp,
            pid,
            "Pizza",
            20.0,
            payer=alice["id"],
            payed_for=[alice["id"], bob["id"]],
            comment="With pepperoni",
            date="2026-04-26",
        )
        bill = json.loads(await nc_mcp.call("get_cospend_bill", project_id=pid, bill_id=bill_id))
        assert bill["what"] == "Pizza"
        assert bill["amount"] == 20.0
        assert bill["payer_id"] == alice["id"]
        assert sorted(bill["owerIds"]) == sorted([alice["id"], bob["id"]])
        assert bill["comment"] == "With pepperoni"
        assert bill["date"] == "2026-04-26"

    @pytest.mark.asyncio
    async def test_list_includes_created_bill(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-bill-list"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        await _make_bill(nc_mcp, pid, "Cake", 5.0, alice["id"], [alice["id"], bob["id"]])
        await _make_bill(nc_mcp, pid, "Beer", 8.0, bob["id"], [alice["id"], bob["id"]])
        result = json.loads(await nc_mcp.call("list_cospend_bills", project_id=pid))
        assert result["nb_bills"] == 2
        whats = sorted(b["what"] for b in result["bills"])
        assert whats == ["Beer", "Cake"]

    @pytest.mark.asyncio
    async def test_list_search_term_filters(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-bill-search"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        await _make_bill(nc_mcp, pid, "Pizza", 20.0, alice["id"], [alice["id"], bob["id"]])
        await _make_bill(nc_mcp, pid, "Sushi", 30.0, alice["id"], [alice["id"], bob["id"]])
        # Cospend quirk: search_term is only applied when limit is also set.
        # nb_bills stays at the pre-search project total — only `bills` is filtered.
        result = json.loads(await nc_mcp.call("list_cospend_bills", project_id=pid, search_term="izz", limit=10))
        whats = [b["what"] for b in result["bills"]]
        assert whats == ["Pizza"]
        assert result["nb_bills"] == 2  # search doesn't affect the count

    @pytest.mark.asyncio
    async def test_list_search_term_without_limit_raises(self, nc_mcp: McpTestHelper) -> None:
        """search_term without limit must fail fast — Cospend silently ignores it otherwise."""
        pid = "mcp-test-bill-searchnolim"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        await _make_member(nc_mcp, pid, "Bob")
        await _make_bill(nc_mcp, pid, "Pizza", 20.0, alice["id"], [alice["id"]])
        with pytest.raises(ToolError, match="limit is required"):
            await nc_mcp.call("list_cospend_bills", project_id=pid, search_term="Pizz")

    @pytest.mark.asyncio
    async def test_list_payer_id_filters(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-bill-payer"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        await _make_bill(nc_mcp, pid, "Cake", 5.0, alice["id"], [alice["id"], bob["id"]])
        await _make_bill(nc_mcp, pid, "Beer", 8.0, bob["id"], [alice["id"], bob["id"]])
        result = json.loads(await nc_mcp.call("list_cospend_bills", project_id=pid, payer_id=alice["id"]))
        assert result["nb_bills"] == 1
        assert result["bills"][0]["what"] == "Cake"

    @pytest.mark.asyncio
    async def test_update_changes_amount(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-bill-update"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        bill_id = await _make_bill(nc_mcp, pid, "Pizza", 10.0, alice["id"], [alice["id"], bob["id"]])
        result = json.loads(
            await nc_mcp.call(
                "update_cospend_bill",
                project_id=pid,
                bill_id=bill_id,
                amount=15.0,
            )
        )
        assert result == {"bill_id": bill_id}
        bill = json.loads(await nc_mcp.call("get_cospend_bill", project_id=pid, bill_id=bill_id))
        assert bill["amount"] == 15.0

    @pytest.mark.asyncio
    async def test_update_changes_payed_for_replaces(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-bill-payedfor"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        carol = await _make_member(nc_mcp, pid, "Carol")
        bill_id = await _make_bill(nc_mcp, pid, "Pizza", 10.0, alice["id"], [alice["id"], bob["id"]])
        await nc_mcp.call(
            "update_cospend_bill",
            project_id=pid,
            bill_id=bill_id,
            payed_for=[bob["id"], carol["id"]],
        )
        bill = json.loads(await nc_mcp.call("get_cospend_bill", project_id=pid, bill_id=bill_id))
        assert sorted(bill["owerIds"]) == sorted([bob["id"], carol["id"]])

    @pytest.mark.asyncio
    async def test_delete_with_trash_keeps_bill_recoverable(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-bill-trash"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        bill_id = await _make_bill(nc_mcp, pid, "Pizza", 10.0, alice["id"], [alice["id"], bob["id"]])
        result = json.loads(await nc_mcp.call("delete_cospend_bill", project_id=pid, bill_id=bill_id))
        assert result == {"project_id": pid, "bill_id": bill_id, "moved_to_trash": True}
        # live list should not include it
        live = json.loads(await nc_mcp.call("list_cospend_bills", project_id=pid))
        assert live["nb_bills"] == 0
        # trashed list should include it
        trashed = json.loads(await nc_mcp.call("list_cospend_bills", project_id=pid, deleted=1))
        assert trashed["nb_bills"] == 1
        assert trashed["bills"][0]["id"] == bill_id

    @pytest.mark.asyncio
    async def test_delete_bill_blocked_when_deletion_disabled(self, nc_mcp: McpTestHelper) -> None:
        """deletionDisabled is enforced by delete_cospend_bill — server returns HTTP 403.

        Counters the (incorrect) intuition that the flag is a frontend-only hint.
        """
        pid = "mcp-test-bill-deletion-disabled"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        bill_id = await _make_bill(nc_mcp, pid, "Pizza", 10.0, alice["id"], [alice["id"], bob["id"]])

        await nc_mcp.call("update_cospend_project", project_id=pid, deletion_disabled=True)

        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("delete_cospend_bill", project_id=pid, bill_id=bill_id)

        # Bill is still alive
        bill = json.loads(await nc_mcp.call("get_cospend_bill", project_id=pid, bill_id=bill_id))
        assert bill["deleted"] == 0

        # delete_cospend_project is NOT gated by deletionDisabled — must succeed
        result = json.loads(await nc_mcp.call("delete_cospend_project", project_id=pid))
        assert result["message"] == "DELETED"

    @pytest.mark.asyncio
    async def test_delete_without_trash_purges_bill(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-bill-purge"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        bill_id = await _make_bill(nc_mcp, pid, "Pizza", 10.0, alice["id"], [alice["id"], bob["id"]])
        await nc_mcp.call("delete_cospend_bill", project_id=pid, bill_id=bill_id, move_to_trash=False)
        # neither live nor trashed lists should include it
        live = json.loads(await nc_mcp.call("list_cospend_bills", project_id=pid))
        assert live["nb_bills"] == 0
        trashed = json.loads(await nc_mcp.call("list_cospend_bills", project_id=pid, deleted=1))
        assert trashed["nb_bills"] == 0


class TestStatisticsAndSettlement:
    @pytest.mark.asyncio
    async def test_statistics_reflects_bill_split(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-stats"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        await _make_bill(nc_mcp, pid, "Pizza", 30.0, alice["id"], [alice["id"], bob["id"]])
        result = json.loads(await nc_mcp.call("get_cospend_project_statistics", project_id=pid))
        stats = {s["member"]["name"]: s for s in result["stats"]}
        assert stats["Alice"]["paid"] == 30.0
        assert stats["Bob"]["paid"] == 0
        # both share the cost equally
        assert round(stats["Alice"]["balance"], 6) == 15.0
        assert round(stats["Bob"]["balance"], 6) == -15.0

    @pytest.mark.asyncio
    async def test_settlement_proposes_transactions(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-settle"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        await _make_bill(nc_mcp, pid, "Pizza", 30.0, alice["id"], [alice["id"], bob["id"]])
        result = json.loads(await nc_mcp.call("get_cospend_project_settlement", project_id=pid))
        assert "transactions" in result
        # Bob owes Alice 15
        assert any(
            t["from"] == bob["id"] and t["to"] == alice["id"] and round(t["amount"], 6) == 15.0
            for t in result["transactions"]
        )

    @pytest.mark.asyncio
    async def test_statistics_filter_by_payer(self, nc_mcp: McpTestHelper) -> None:
        pid = "mcp-test-stats-filter"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        await _make_bill(nc_mcp, pid, "Pizza", 20.0, alice["id"], [alice["id"], bob["id"]])
        await _make_bill(nc_mcp, pid, "Beer", 10.0, bob["id"], [alice["id"], bob["id"]])
        result = json.loads(await nc_mcp.call("get_cospend_project_statistics", project_id=pid, payer_id=alice["id"]))
        # filtered_balance reflects only the filtered slice (Alice's bills)
        stats = {s["member"]["name"]: s for s in result["stats"]}
        assert round(stats["Alice"]["filtered_balance"], 6) == 10.0
        assert round(stats["Bob"]["filtered_balance"], 6) == -10.0


class TestPermissionGating:
    """Verify that permission levels block higher-tier operations."""

    @pytest.mark.asyncio
    async def test_read_only_blocks_create_project(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="permission"):
            await nc_mcp_read_only.call("create_cospend_project", project_id="mcp-test-perm", name="Should Fail")

    @pytest.mark.asyncio
    async def test_write_blocks_delete_project(self, nc_mcp: McpTestHelper, nc_mcp_write: McpTestHelper) -> None:
        # Use the destructive helper to set up the project so we have one to attempt to delete
        await _make_project(nc_mcp, "mcp-test-perm-del")
        with pytest.raises(ToolError, match="permission"):
            await nc_mcp_write.call("delete_cospend_project", project_id="mcp-test-perm-del")

    @pytest.mark.asyncio
    async def test_write_blocks_delete_bill(self, nc_mcp: McpTestHelper, nc_mcp_write: McpTestHelper) -> None:
        pid = "mcp-test-perm-bill"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        bill_id = await _make_bill(nc_mcp, pid, "Pizza", 10.0, alice["id"], [alice["id"], bob["id"]])
        with pytest.raises(ToolError, match="permission"):
            await nc_mcp_write.call("delete_cospend_bill", project_id=pid, bill_id=bill_id)

    @pytest.mark.asyncio
    async def test_read_only_can_call_read_tools(self, nc_mcp_read_only: McpTestHelper) -> None:
        # list_cospend_projects requires READ — must work under read-only
        result = await nc_mcp_read_only.call("list_cospend_projects")
        assert isinstance(json.loads(result), list)


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_get_member_list_for_nonexistent_project_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("list_cospend_members", project_id="mcp-test-no-such")

    @pytest.mark.asyncio
    async def test_get_bill_with_bad_id_raises(self, nc_mcp: McpTestHelper) -> None:
        await _make_project(nc_mcp, "mcp-test-bad-bill")
        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("get_cospend_bill", project_id="mcp-test-bad-bill", bill_id=999_999_999)

    @pytest.mark.asyncio
    async def test_create_bill_with_empty_payed_for_rejected(self, nc_mcp: McpTestHelper) -> None:
        """payed_for=[] is rejected client-side — server would 400, we fail fast with a clearer message."""
        pid = "mcp-test-bill-empty-create"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        with pytest.raises(ToolError, match="non-empty"):
            await nc_mcp.call(
                "create_cospend_bill",
                project_id=pid,
                what="X",
                amount=1.0,
                payer=alice["id"],
                payed_for=[],
            )

    @pytest.mark.asyncio
    async def test_update_bill_with_empty_payed_for_rejected(self, nc_mcp: McpTestHelper) -> None:
        """payed_for=[] on update is rejected — server would silently no-op (200 OK with owers unchanged),
        which would look like a successful update. We reject up front instead."""
        pid = "mcp-test-bill-empty-update"
        await _make_project(nc_mcp, pid)
        alice = await _make_member(nc_mcp, pid, "Alice")
        bob = await _make_member(nc_mcp, pid, "Bob")
        bill_id = await _make_bill(nc_mcp, pid, "X", 1.0, alice["id"], [alice["id"], bob["id"]])
        with pytest.raises(ToolError, match="non-empty"):
            await nc_mcp.call("update_cospend_bill", project_id=pid, bill_id=bill_id, payed_for=[])


class TestToolRegistration:
    """Confirm all expected tools are registered (catches accidental drops)."""

    EXPECTED_TOOLS: ClassVar[set[str]] = {
        "list_cospend_projects",
        "get_cospend_project",
        "create_cospend_project",
        "update_cospend_project",
        "delete_cospend_project",
        "get_cospend_project_statistics",
        "get_cospend_project_settlement",
        "list_cospend_members",
        "create_cospend_member",
        "update_cospend_member",
        "delete_cospend_member",
        "list_cospend_bills",
        "get_cospend_bill",
        "create_cospend_bill",
        "update_cospend_bill",
        "delete_cospend_bill",
    }

    def test_all_cospend_tools_registered(self, nc_mcp: McpTestHelper) -> None:
        registered = set(nc_mcp.tool_names())
        missing = self.EXPECTED_TOOLS - registered
        assert not missing, f"missing cospend tools: {sorted(missing)}"
