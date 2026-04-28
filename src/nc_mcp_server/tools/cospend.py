"""Cospend tools — OCS API for shared expense tracking (projects, members, bills)."""

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote as url_quote

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

API_BASE = "apps/cospend/api/v1"


def _body(**kwargs: Any) -> dict[str, Any]:
    """Build a JSON body dict, dropping any None values."""
    return {k: v for k, v in kwargs.items() if v is not None}


def _pid(project_id: str) -> str:
    """URL-encode a project id for use in a path segment.

    Cospend allows project ids with spaces (and other characters) in `id` since
    the slug is user-supplied and only `/` is rejected. Without encoding, any
    such id breaks URL parsing on subsequent calls.
    """
    return url_quote(project_id, safe="")


def _register_project_reads(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_cospend_projects() -> str:
        """List Cospend (shared expense) projects the current user can access.

        Returns:
            JSON array of full project objects. Each entry includes id (string
            slug, used as projectId in other tools), name, userid (owner),
            email, lastchanged, deletiondisabled, archived_ts, currencyname,
            categorysort/paymentmodesort, and `myaccesslevel` (1=VIEWER,
            2=PARTICIPANT, 3=MAINTAINER, 4=ADMIN). Also embeds members,
            balance, shares, currencies, categories, paymentmodes for each
            project, and counters nb_bills / total_spent / nb_trashbin_bills.
        """
        client = get_client()
        data = await client.ocs_get(f"{API_BASE}/projects")
        return json.dumps(data)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_cospend_project(project_id: str) -> str:
        """Get full info for a single Cospend project (members, balance, shares, …).

        Requires VIEWER access on the project.

        Args:
            project_id: String project id (slug).

        Returns:
            JSON object with the same shape as one entry from
            list_cospend_projects. Use `members` for member ids/names and
            `balance` for the per-member balance map.
        """
        client = get_client()
        data = await client.ocs_get(f"{API_BASE}/projects/{_pid(project_id)}")
        return json.dumps(data)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_cospend_project_statistics(
        project_id: str,
        ts_min: int | None = None,
        ts_max: int | None = None,
        payment_mode_id: int | None = None,
        category_id: int | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        currency_id: int | None = None,
        payer_id: int | None = None,
        show_disabled: bool = True,
    ) -> str:
        """Per-member spending statistics for a Cospend project.

        Requires VIEWER access. All filters are optional and AND-combined.

        Args:
            project_id: String project id.
            ts_min: Only include bills with timestamp >= ts_min (Unix seconds).
            ts_max: Only include bills with timestamp <= ts_max.
            payment_mode_id: Filter by payment mode.
            category_id: Filter by category.
            amount_min: Only include bills with amount >= amount_min.
            amount_max: Only include bills with amount <= amount_max.
            currency_id: Convert/filter by currency id.
            payer_id: Only include bills paid by this member.
            show_disabled: Include disabled members in the output.

        Returns:
            JSON object with `stats` (list of {member, balance, paid, spent,
            filtered_balance}), plus aggregates such as memberMonthlyStats /
            categoryStats depending on filters.
        """
        client = get_client()
        params = _body(
            tsMin=ts_min,
            tsMax=ts_max,
            paymentModeId=payment_mode_id,
            categoryId=category_id,
            amountMin=amount_min,
            amountMax=amount_max,
            currencyId=currency_id,
            payerId=payer_id,
        )
        params["showDisabled"] = "1" if show_disabled else "0"
        data = await client.ocs_get(f"{API_BASE}/projects/{_pid(project_id)}/statistics", params=params)
        return json.dumps(data)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_cospend_project_settlement(
        project_id: str,
        centered_on: int | None = None,
        max_timestamp: int | None = None,
    ) -> str:
        """Suggested reimbursement transactions to settle a Cospend project.

        Requires VIEWER access.

        Args:
            project_id: String project id.
            centered_on: Member id to center the plan on. All suggested
                transactions will involve this member (e.g. "everyone pays Alice").
            max_timestamp: Settle up to this date (Unix seconds). Member
                balances will be zero at this date and bills after it are
                ignored.

        Returns:
            JSON object with `transactions` (list of {from, to, amount} —
            from/to are member ids) and `balances` (map of member-id →
            current balance).
        """
        client = get_client()
        params = _body(centeredOn=centered_on, maxTimestamp=max_timestamp)
        data = await client.ocs_get(
            f"{API_BASE}/projects/{_pid(project_id)}/settlement",
            params=params or None,
        )
        return json.dumps(data)


def _register_member_reads(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_cospend_members(project_id: str, last_changed: int | None = None) -> str:
        """List members of a Cospend project.

        Requires VIEWER access.

        Args:
            project_id: String project id.
            last_changed: If provided, only return members modified after this
                Unix timestamp (used for incremental sync by clients).

        Returns:
            JSON array of members. Each entry has id (integer member id, used
            as memberId in other tools), name, weight (share weight, default
            1), activated (bool — false means soft-disabled but kept for bill
            history), userid (linked Nextcloud user id, or null for free-form
            members), color (RGB dict), lastchanged.
        """
        client = get_client()
        params = {"lastChanged": last_changed} if last_changed is not None else None
        data = await client.ocs_get(f"{API_BASE}/projects/{_pid(project_id)}/members", params=params)
        return json.dumps(data)


def _register_bill_reads(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_cospend_bills(
        project_id: str,
        offset: int | None = None,
        limit: int | None = None,
        reverse: bool = False,
        last_changed: int | None = None,
        payer_id: int | None = None,
        category_id: int | None = None,
        payment_mode_id: int | None = None,
        include_bill_id: int | None = None,
        search_term: str | None = None,
        deleted: int = 0,
    ) -> str:
        """List bills (expenses) in a Cospend project.

        Requires VIEWER access.

        Args:
            project_id: String project id.
            offset: Skip the first N bills (server orders by date desc by default).
            limit: Max bills to return. Omit for no limit.
            reverse: If True, oldest-first instead of newest-first.
            last_changed: Only return bills modified after this Unix timestamp.
            payer_id: Filter to bills paid by this member.
            category_id: Filter by category.
            payment_mode_id: Filter by payment mode.
            include_bill_id: Force-include this bill id in the result even if
                it would be paginated out (used to keep a focused bill in view).
            search_term: Substring match (case-insensitive) on bill `what`,
                `comment`, or amount±1. REQUIRES `limit` to be set — Cospend
                silently ignores the search and returns unfiltered results
                when no limit is provided, so this tool raises ValueError if
                `search_term` is given without `limit`. Pass any limit
                (e.g. 1000) to apply the filter.
            deleted: 0 = live bills (default), 1 = trashed bills only.

        Returns:
            JSON object with `bills` (list of bill dicts — see get_cospend_bill
            for fields), `nb_bills` (project-wide bill count under the
            payer/category/payment-mode/deleted filters; NOT affected by
            `search_term` even when search filters `bills`), `allBillIds`
            (full id list before pagination), `timestamp` (server response time).
        """
        if search_term is not None and limit is None:
            raise ValueError(
                "limit is required when search_term is set (Cospend silently ignores the search otherwise)"
            )
        client = get_client()
        params = _body(
            offset=offset,
            limit=limit,
            lastChanged=last_changed,
            payerId=payer_id,
            categoryId=category_id,
            paymentModeId=payment_mode_id,
            includeBillId=include_bill_id,
            searchTerm=search_term,
        )
        params["deleted"] = deleted
        params["reverse"] = "true" if reverse else "false"
        data = await client.ocs_get(f"{API_BASE}/projects/{_pid(project_id)}/bills", params=params)
        return json.dumps(data)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_cospend_bill(project_id: str, bill_id: int) -> str:
        """Get a single Cospend bill (expense) by id. Requires VIEWER access.

        Args:
            project_id: String project id.
            bill_id: Integer bill id.

        Returns:
            JSON bill object: id, what (description), amount, payer_id,
            owers (list of member dicts who share the cost), owerIds (id list),
            date (YYYY-MM-DD), timestamp (Unix seconds), comment, categoryid,
            paymentmodeid, repeat ("n"=none, "d"=daily, "w"=weekly,
            "b"=biweekly, "s"=semi-monthly, "m"=monthly, "y"=yearly),
            repeatfreq, repeatallactive, repeatuntil, deleted (0=live,
            1=in trash), lastchanged.
        """
        client = get_client()
        data = await client.ocs_get(f"{API_BASE}/projects/{_pid(project_id)}/bills/{bill_id}")
        return json.dumps(data)


def _register_project_writes(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_cospend_project(project_id: str, name: str) -> str:
        """Create a new Cospend project. The caller becomes ADMIN of it.

        Args:
            project_id: Desired string id (slug). If the id is already taken,
                the server appends a digit to make it unique — check the `id`
                field of the returned project for the actual id assigned.
            name: Display name (does not have to be unique).

        Returns:
            JSON of the new project (full info — same shape as
            get_cospend_project), including the resolved id and the default
            categories and payment modes that the server seeds.
        """
        client = get_client()
        data = await client.ocs_post_json(
            f"{API_BASE}/projects",
            json_data={"id": project_id, "name": name},
        )
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_cospend_project(
        project_id: str,
        name: str | None = None,
        auto_export: str | None = None,
        currency_name: str | None = None,
        deletion_disabled: bool | None = None,
        category_sort: str | None = None,
        payment_mode_sort: str | None = None,
        archived_ts: int | None = None,
    ) -> str:
        """Update a Cospend project's settings. Requires ADMIN access.

        Pass only the fields you want to change; omit the rest.

        Args:
            project_id: String project id.
            name: New display name.
            auto_export: Periodic CSV auto-export frequency. Same code set as
                bill `repeat`: "n"=none (default), "d"=daily, "w"=weekly,
                "b"=biweekly, "s"=semi-monthly, "m"=monthly, "y"=yearly.
            currency_name: Main currency name (free-form string, e.g. "EUR").
                Pass empty string to clear.
            deletion_disabled: When set, delete_cospend_bill returns HTTP 403
                ("project deletion is disabled"). delete_cospend_project is
                NOT gated by this flag and still succeeds. Useful as a guard
                against accidental bill removal in shared projects.
            category_sort: Default category ordering. "a"=alphabetical (default),
                "m"=manual (custom `order` field), "u"=most used,
                "r"=recently used.
            payment_mode_sort: Same options as category_sort, for payment modes.
            archived_ts: Archive control with three special values.
                - 0  → archive now (server records the current Unix timestamp).
                - -1 → unarchive (clears the field).
                - any other int → archive at that exact Unix timestamp.
                Note: 0 ARCHIVES the project (it does not unarchive).

        Returns:
            JSON {"project_id": ..., "updated": true} — the OCS endpoint
            returns no body, so this is a synthetic confirmation.
        """
        client = get_client()
        body = _body(
            name=name,
            autoExport=auto_export,
            currencyName=currency_name,
            deletionDisabled=deletion_disabled,
            categorySort=category_sort,
            paymentModeSort=payment_mode_sort,
            archivedTs=archived_ts,
        )
        await client.ocs_put_json(f"{API_BASE}/projects/{_pid(project_id)}", json_data=body)
        return json.dumps({"project_id": project_id, "updated": True})


def _register_member_writes(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_cospend_member(
        project_id: str,
        name: str,
        user_id: str | None = None,
        weight: float = 1.0,
        active: bool = True,
        color: str | None = None,
    ) -> str:
        """Add a member to a Cospend project. Requires MAINTAINER access.

        Args:
            project_id: String project id.
            name: Display name of the new member.
            user_id: Link this member to a Nextcloud user id (optional). If set,
                the member can see the project in their Cospend UI.
            weight: Share weight (default 1.0). A weight-2 member counts double
                in even splits.
            active: If False, the member is created soft-disabled (rare —
                usually create active and disable later via update_cospend_member).
            color: Hex color like "#aabbcc". Omit to let the server pick.

        Returns:
            JSON of the new member: id (use as memberId in other tools), name,
            weight, activated, userid, color (RGB dict), lastchanged.
        """
        client = get_client()
        body = _body(name=name, weight=weight, active=1 if active else 0, userId=user_id, color=color)
        data = await client.ocs_post_json(f"{API_BASE}/projects/{_pid(project_id)}/members", json_data=body)
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_cospend_member(
        project_id: str,
        member_id: int,
        name: str | None = None,
        weight: float | None = None,
        activated: bool | None = None,
        color: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Update a Cospend member. Requires MAINTAINER access.

        Pass only the fields you want to change.

        IMPORTANT: Setting `activated=False` on a member who has no bills will
        permanently delete them, not just disable them. To always keep a
        recoverable record, ensure the member has at least one associated bill
        first, or use delete_cospend_member which is unambiguous.

        Args:
            project_id: String project id.
            member_id: Integer member id (from list_cospend_members).
            name: New display name.
            weight: New share weight.
            activated: True = active, False = disabled (or deleted if no bills).
            color: New hex color (with or without leading "#"). Pass empty
                string to clear the color (server picks one on next display).
            user_id: Link/unlink to a Nextcloud user id. Pass empty string to
                unlink.

        Returns:
            JSON of the updated member, or null if the member was deleted as
            described above.
        """
        client = get_client()
        body = _body(name=name, weight=weight, activated=activated, color=color, userId=user_id)
        data = await client.ocs_put_json(
            f"{API_BASE}/projects/{_pid(project_id)}/members/{member_id}",
            json_data=body,
        )
        return json.dumps(data)


def _register_bill_writes(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_cospend_bill(
        project_id: str,
        what: str,
        amount: float,
        payer: int,
        payed_for: list[int],
        date: str | None = None,
        timestamp: int | None = None,
        comment: str | None = None,
        category_id: int | None = None,
        payment_mode_id: int | None = None,
        repeat: str = "n",
        repeat_freq: int | None = None,
        repeat_until: str | None = None,
        repeat_all_active: int = 0,
    ) -> str:
        """Create a bill (expense) in a Cospend project. Requires PARTICIPANT access.

        Args:
            project_id: String project id.
            what: Short description of the expense (e.g. "Pizza").
            amount: Total amount paid.
            payer: Member id of the person who paid.
            payed_for: Non-empty list of member ids who share the cost.
                Cospend requires at least one ower per bill — passing [] raises
                ValueError (the server would reject it on create and silently
                no-op on update, so we reject it up front for both).
            date: Date string "YYYY-MM-DD". Defaults to today (UTC) if both
                date and timestamp are omitted; the underlying API requires one.
            timestamp: Alternative to date — Unix seconds. If both are set, the
                server uses timestamp.
            comment: Free-form longer note.
            category_id: Category id (see project's `categories`). Omit for
                uncategorized (id=0).
            payment_mode_id: Payment mode id (see project's `paymentmodes`).
                Omit for unset (id=0).
            repeat: Repetition mode: "n"=no repeat (default), "d"=daily,
                "w"=weekly, "b"=biweekly, "s"=semi-monthly, "m"=monthly,
                "y"=yearly.
            repeat_freq: Every N units (default 1). E.g. repeat="m",
                repeat_freq=3 means quarterly.
            repeat_until: Stop repeating after this date "YYYY-MM-DD".
            repeat_all_active: 0 (default) = repeat with the same owers,
                1 = on each repetition use whoever is currently active.

        Returns:
            JSON {"bill_id": <int>} — the integer id of the new bill.
        """
        if not payed_for:
            raise ValueError("payed_for must be a non-empty list of member ids")
        client = get_client()
        if timestamp is None and date is None:
            date = datetime.now(UTC).date().isoformat()
        body = _body(
            what=what,
            amount=amount,
            payer=payer,
            payedFor=",".join(str(m) for m in payed_for),
            repeat=repeat,
            repeatAllActive=repeat_all_active,
            timestamp=timestamp,
            date=date,
            comment=comment,
            categoryId=category_id,
            paymentModeId=payment_mode_id,
            repeatFreq=repeat_freq,
            repeatUntil=repeat_until,
        )
        bill_id = await client.ocs_post_json(f"{API_BASE}/projects/{_pid(project_id)}/bills", json_data=body)
        return json.dumps({"bill_id": bill_id})

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_cospend_bill(
        project_id: str,
        bill_id: int,
        what: str | None = None,
        amount: float | None = None,
        payer: int | None = None,
        payed_for: list[int] | None = None,
        date: str | None = None,
        timestamp: int | None = None,
        comment: str | None = None,
        category_id: int | None = None,
        payment_mode_id: int | None = None,
        repeat: str | None = None,
        repeat_freq: int | None = None,
        repeat_until: str | None = None,
        repeat_all_active: int | None = None,
        deleted: int | None = None,
    ) -> str:
        """Update a Cospend bill. Requires PARTICIPANT access.

        Pass only fields you want to change. See create_cospend_bill for
        field semantics.

        Args:
            project_id: String project id.
            bill_id: Integer bill id.
            what: New description.
            amount: New amount.
            payer: New payer member id.
            payed_for: New non-empty list of ower member ids (replaces,
                doesn't merge). Passing [] raises ValueError — the server
                no-ops silently on empty payedFor, which would look like a
                successful update but leave owers unchanged.
            date: New "YYYY-MM-DD" date.
            timestamp: Alternative to date.
            comment: New comment.
            category_id: New category id (0 = uncategorized).
            payment_mode_id: New payment mode id (0 = unset).
            repeat: New repeat mode ("n", "d", "w", "b", "s", "m", "y").
            repeat_freq: New repeat frequency.
            repeat_until: New stop date "YYYY-MM-DD". Pass empty string to
                clear (repeat indefinitely).
            repeat_all_active: New owers behavior on repeat.
            deleted: 0 = restore from trash, 1 = move to trash. Use
                delete_cospend_bill for trashing in normal flow.

        Returns:
            JSON {"bill_id": <int>} confirming the updated bill id.
        """
        if payed_for is not None and not payed_for:
            raise ValueError("payed_for must be a non-empty list of member ids")
        client = get_client()
        body = _body(
            what=what,
            amount=amount,
            payer=payer,
            payedFor=",".join(str(m) for m in payed_for) if payed_for is not None else None,
            date=date,
            timestamp=timestamp,
            comment=comment,
            categoryId=category_id,
            paymentModeId=payment_mode_id,
            repeat=repeat,
            repeatFreq=repeat_freq,
            repeatUntil=repeat_until,
            repeatAllActive=repeat_all_active,
            deleted=deleted,
        )
        result = await client.ocs_put_json(
            f"{API_BASE}/projects/{_pid(project_id)}/bills/{bill_id}",
            json_data=body,
        )
        return json.dumps({"bill_id": result})


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_cospend_project(project_id: str) -> str:
        """Delete a Cospend project and all its members, bills, and shares.

        Requires ADMIN access on the project. The project-delete endpoint does
        NOT honor `deletionDisabled` (only delete_cospend_bill is gated by
        that flag), so this irrevocably removes everything regardless.

        Args:
            project_id: String project id.

        Returns:
            JSON {"project_id": ..., "message": "DELETED"}.
        """
        client = get_client()
        await client.ocs_delete(f"{API_BASE}/projects/{_pid(project_id)}")
        return json.dumps({"project_id": project_id, "message": "DELETED"})

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_cospend_member(project_id: str, member_id: int) -> str:
        """Delete (or disable) a Cospend project member. Requires MAINTAINER access.

        Members with bills cannot be hard-deleted — they are soft-disabled
        instead (activated=false) so existing bill history stays valid. Members
        without any bill are permanently removed.

        Args:
            project_id: String project id.
            member_id: Integer member id.

        Returns:
            JSON {"project_id": ..., "member_id": ..., "deleted": true}.
        """
        client = get_client()
        await client.ocs_delete(f"{API_BASE}/projects/{_pid(project_id)}/members/{member_id}")
        return json.dumps({"project_id": project_id, "member_id": member_id, "deleted": True})

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_cospend_bill(
        project_id: str,
        bill_id: int,
        move_to_trash: bool = True,
    ) -> str:
        """Delete a Cospend bill. Requires PARTICIPANT access.

        Returns HTTP 403 ("project deletion is disabled") if the project has
        `deletionDisabled` set. Use update_cospend_project to clear that flag
        first if you need to delete bills.

        Args:
            project_id: String project id.
            bill_id: Integer bill id.
            move_to_trash: If True (default), move to the project trash bin —
                the bill can be restored later by setting deleted=0 via
                update_cospend_bill. If False, hard-delete (irreversible).

        Returns:
            JSON {"project_id": ..., "bill_id": ..., "moved_to_trash": <bool>}.
        """
        client = get_client()
        await client.ocs_delete(
            f"{API_BASE}/projects/{_pid(project_id)}/bills/{bill_id}?moveToTrash={'true' if move_to_trash else 'false'}"
        )
        return json.dumps({"project_id": project_id, "bill_id": bill_id, "moved_to_trash": move_to_trash})


def register(mcp: FastMCP) -> None:
    """Register Cospend tools with the MCP server."""
    _register_project_reads(mcp)
    _register_member_reads(mcp)
    _register_bill_reads(mcp)
    _register_project_writes(mcp)
    _register_member_writes(mcp)
    _register_bill_writes(mcp)
    _register_destructive_tools(mcp)
