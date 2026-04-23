"""Forms tools — OCS v3 API for Nextcloud Forms app (surveys, polls, questionnaires)."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

QUESTION_TYPES = {
    "short",
    "long",
    "multiple",
    "multiple_unique",
    "dropdown",
    "date",
    "time",
    "file",
    "grid",
    "color",
    "linearscale",
}
GRID_SUBTYPES = {"radio", "checkbox", "number"}


def _dedupe_forms_by_id(*batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge form lists by id, preserving order and dropping duplicates."""
    seen: set[int] = set()
    merged: list[dict[str, Any]] = []
    for batch in batches:
        for form in batch:
            fid: int | None = form.get("id")
            if fid is None or fid in seen:
                continue
            seen.add(fid)
            merged.append(form)
    return merged


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_forms(ownership: str | None = None) -> str:
        """List forms visible to the current user.

        Args:
            ownership: Filter. One of: "owned" (forms I created), "shared"
                (forms shared with me). Omit to get both — the Nextcloud
                endpoint takes one filter at a time, so this tool calls it
                twice and merges the results when omitted.

        Returns:
            JSON array of form summaries. Each entry includes id, hash, title,
            state (0=active, 1=closed, 2=archived), permissions, and metadata.
            Call get_form(id) for full details including questions.
        """
        client = get_client()
        if ownership is not None:
            data = await client.ocs_get("apps/forms/api/v3/forms", params={"type": ownership})
            return json.dumps(data)
        owned = await client.ocs_get("apps/forms/api/v3/forms", params={"type": "owned"})
        shared = await client.ocs_get("apps/forms/api/v3/forms", params={"type": "shared"})
        return json.dumps(_dedupe_forms_by_id(owned, shared))

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_form(form_id: int) -> str:
        """Get a form's full definition including questions, options, and shares.

        Args:
            form_id: Numeric form id from list_forms.

        Returns:
            JSON object with the full form: title, description, access, expires,
            isAnonymous, submitMultiple, state, maxSubmissions, questions (each
            with options, type, isRequired, etc.), shares, and submissionCount.
            Does NOT include submission answers — use list_submissions for those.
        """
        client = get_client()
        data = await client.ocs_get(f"apps/forms/api/v3/forms/{form_id}")
        return json.dumps(data)


def _register_question_reads(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_questions(form_id: int) -> str:
        """List all questions on a form (same data as get_form.questions).

        Args:
            form_id: Numeric form id.

        Returns:
            JSON array of questions, each with id, type, text, description,
            isRequired, order, options (for choice questions), and extraSettings.
        """
        client = get_client()
        data = await client.ocs_get(f"apps/forms/api/v3/forms/{form_id}/questions")
        return json.dumps(data)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_question(form_id: int, question_id: int) -> str:
        """Get a single question including its options.

        Args:
            form_id: Numeric form id.
            question_id: Numeric question id.

        Returns:
            JSON object with the question's full definition.
        """
        client = get_client()
        data = await client.ocs_get(f"apps/forms/api/v3/forms/{form_id}/questions/{question_id}")
        return json.dumps(data)


def _register_submission_reads(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_submissions(
        form_id: int,
        query: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> str:
        """List submissions (responses) to a form. Only the form owner can view submissions.

        Args:
            form_id: Numeric form id.
            query: Optional full-text filter across answer text.
            limit: Max submissions to return (for pagination).
            offset: Starting offset (for pagination).

        Returns:
            JSON object with fields: submissions (array of submission objects,
            each with id, userId, timestamp, answers), questions (the form's
            questions at submission time), filteredSubmissionsCount.
        """
        client = get_client()
        params: dict[str, Any] = {}
        if query is not None:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        data = await client.ocs_get(
            f"apps/forms/api/v3/forms/{form_id}/submissions",
            params=params or None,
        )
        return json.dumps(data)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_submission(form_id: int, submission_id: int) -> str:
        """Get a single submission by id, including all answers.

        Args:
            form_id: Numeric form id.
            submission_id: Numeric submission id.

        Returns:
            JSON object with the submission's userId, timestamp, and answers array.
        """
        client = get_client()
        data = await client.ocs_get(f"apps/forms/api/v3/forms/{form_id}/submissions/{submission_id}")
        return json.dumps(data)


def _register_form_writes(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_form(from_id: int | None = None) -> str:
        """Create a new form. Returns the form with a generated id and default empty title.

        Args:
            from_id: Optional id of an existing form to clone (copies questions
                and options; does not copy submissions or shares).

        Returns:
            JSON of the new form. Use update_form to set title/description,
            then create_question to add questions.
        """
        client = get_client()
        body = {"fromId": from_id} if from_id is not None else {}
        data = await client.ocs_post_json("apps/forms/api/v3/forms", json_data=body)
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_form(form_id: int, key_value_pairs: dict[str, Any]) -> str:
        """Update a form's properties.

        Args:
            form_id: Numeric form id.
            key_value_pairs: Object with the fields to change. Common keys:
                title (str), description (str), isAnonymous (bool),
                submitMultiple (bool), allowEditSubmissions (bool),
                expires (unix timestamp, 0 = never), showExpiration (bool),
                state (0=active, 1=closed, 2=archived), maxSubmissions (int,
                0 = unlimited), submissionMessage (str), access (object with
                permitAllUsers/showToAllUsers), fileFormat, filePath.

        Returns:
            JSON of the updated form (refetched after the patch for convenience).
        """
        client = get_client()
        await client.ocs_patch_json(
            f"apps/forms/api/v3/forms/{form_id}",
            json_data={"keyValuePairs": key_value_pairs},
        )
        data = await client.ocs_get(f"apps/forms/api/v3/forms/{form_id}")
        return json.dumps(data)


def _register_question_writes(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_question(
        form_id: int,
        question_type: str,
        text: str | None = None,
        subtype: str | None = None,
        from_id: int | None = None,
    ) -> str:
        """Add a question to a form.

        Args:
            form_id: Numeric form id.
            question_type: One of: "short" (single-line text), "long"
                (multi-line text), "multiple" (checkbox), "multiple_unique"
                (radio), "dropdown", "date", "time", "file", "grid", "color",
                "linearscale". "datetime" is rejected by Nextcloud — use
                separate "date" and "time" questions.
            text: Question text. Defaults to empty; update_question can set it later.
            subtype: For question_type="grid" only, the cell type: "radio",
                "checkbox", or "number".
            from_id: Optional id of an existing question to clone.

        Returns:
            JSON of the new question including its assigned id and order.
            For choice types (dropdown/multiple/multiple_unique), use
            create_options to add the answer choices.
        """
        if question_type not in QUESTION_TYPES:
            raise ValueError(f"Invalid question_type '{question_type}'. Must be one of: {sorted(QUESTION_TYPES)}")
        if question_type == "grid" and subtype is not None and subtype not in GRID_SUBTYPES:
            raise ValueError(f"Invalid grid subtype '{subtype}'. Must be one of: {sorted(GRID_SUBTYPES)}")
        client = get_client()
        body: dict[str, Any] = {"type": question_type}
        if text is not None:
            body["text"] = text
        if subtype is not None:
            body["subtype"] = subtype
        if from_id is not None:
            body["fromId"] = from_id
        data = await client.ocs_post_json(
            f"apps/forms/api/v3/forms/{form_id}/questions",
            json_data=body,
        )
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_question(form_id: int, question_id: int, key_value_pairs: dict[str, Any]) -> str:
        """Update a question's properties (cannot change order — use reorder_questions).

        Args:
            form_id: Numeric form id.
            question_id: Numeric question id.
            key_value_pairs: Object with fields to change. Common keys: text (str),
                description (str), isRequired (bool), name (str, alternate id for
                public linking), extraSettings (object; varies by question type).

        Returns:
            JSON of the updated question (refetched after the patch).
        """
        client = get_client()
        await client.ocs_patch_json(
            f"apps/forms/api/v3/forms/{form_id}/questions/{question_id}",
            json_data={"keyValuePairs": key_value_pairs},
        )
        data = await client.ocs_get(f"apps/forms/api/v3/forms/{form_id}/questions/{question_id}")
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def reorder_questions(form_id: int, new_order: list[int]) -> str:
        """Reorder the questions on a form. Must list every question id exactly once.

        Args:
            form_id: Numeric form id.
            new_order: Array of question ids in the desired order.

        Returns:
            JSON with the updated order for each question id.
        """
        client = get_client()
        data = await client.ocs_patch_json(
            f"apps/forms/api/v3/forms/{form_id}/questions",
            json_data={"newOrder": new_order},
        )
        return json.dumps(data)


def _register_option_writes(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_options(form_id: int, question_id: int, option_texts: list[str]) -> str:
        """Add one or more answer options to a choice question (dropdown/multiple/etc.).

        Args:
            form_id: Numeric form id.
            question_id: Numeric question id. Intended for choice-type
                questions (dropdown, multiple, multiple_unique, linearscale,
                grid); options on other types are accepted by the server but
                have no effect.
            option_texts: Array of option labels to create. Each creates a
                separate option in order.

        Returns:
            JSON array of created options with their assigned ids and order.
        """
        client = get_client()
        data = await client.ocs_post_json(
            f"apps/forms/api/v3/forms/{form_id}/questions/{question_id}/options",
            json_data={"optionTexts": option_texts},
        )
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_option(
        form_id: int,
        question_id: int,
        option_id: int,
        key_value_pairs: dict[str, Any],
    ) -> str:
        """Update an option's properties.

        Args:
            form_id: Numeric form id.
            question_id: Numeric question id.
            option_id: Numeric option id.
            key_value_pairs: Object with fields to change. Common keys: text (str).
                Do NOT pass `order` — use reorder_options instead.

        Returns:
            JSON of the updated option (refetched via the parent question).
        """
        client = get_client()
        await client.ocs_patch_json(
            f"apps/forms/api/v3/forms/{form_id}/questions/{question_id}/options/{option_id}",
            json_data={"keyValuePairs": key_value_pairs},
        )
        question = await client.ocs_get(f"apps/forms/api/v3/forms/{form_id}/questions/{question_id}")
        for opt in question.get("options", []):
            if opt.get("id") == option_id:
                return json.dumps(opt)
        return json.dumps({"id": option_id, "updated": True})

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def reorder_options(form_id: int, question_id: int, new_order: list[int]) -> str:
        """Reorder the options within a question.

        Args:
            form_id: Numeric form id.
            question_id: Numeric question id.
            new_order: Array of option ids in the desired order.

        Returns:
            JSON with the updated order for each option id.
        """
        client = get_client()
        data = await client.ocs_patch_json(
            f"apps/forms/api/v3/forms/{form_id}/questions/{question_id}/options",
            json_data={"newOrder": new_order},
        )
        return json.dumps(data)


def _register_share_writes(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_form_share(
        form_id: int,
        share_type: int,
        share_with: str | None = None,
        permissions: list[str] | None = None,
    ) -> str:
        """Share a form with a user, group, circle, or via public link.

        Args:
            form_id: Numeric form id.
            share_type: 0=user, 1=group, 3=link (public), 7=circle.
            share_with: User/group/circle id to share with. Omit for link shares.
            permissions: Array of strings from: "submit" (fill out), "edit"
                (modify form definition), "results" (view submissions),
                "results_delete" (delete submissions), "embed" (render in
                other pages). Defaults to ["submit"].

        Returns:
            JSON of the new share with its id.
        """
        client = get_client()
        body: dict[str, Any] = {"shareType": share_type}
        if share_with is not None:
            body["shareWith"] = share_with
        if permissions is not None:
            body["permissions"] = permissions
        data = await client.ocs_post_json(
            f"apps/forms/api/v3/forms/{form_id}/shares",
            json_data=body,
        )
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_form_share(form_id: int, share_id: int, key_value_pairs: dict[str, Any]) -> str:
        """Update a share's permissions.

        Args:
            form_id: Numeric form id.
            share_id: Numeric share id.
            key_value_pairs: Object with fields to change. Most commonly:
                permissions (array of strings — see create_form_share).

        Returns:
            JSON of the updated share (refetched via the parent form).
        """
        client = get_client()
        await client.ocs_patch_json(
            f"apps/forms/api/v3/forms/{form_id}/shares/{share_id}",
            json_data={"keyValuePairs": key_value_pairs},
        )
        form = await client.ocs_get(f"apps/forms/api/v3/forms/{form_id}")
        for share in form.get("shares", []):
            if share.get("id") == share_id:
                return json.dumps(share)
        return json.dumps({"id": share_id, "updated": True})


def _register_submission_writes(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def submit_form(form_id: int, answers: dict[str, Any], share_hash: str | None = None) -> str:
        """Submit answers to a form on behalf of the current user.

        Args:
            form_id: Numeric form id.
            answers: Object mapping question id (as string) to array of answer
                values. Example: {"42": ["my comment"], "43": [1, 3]} where
                42 is a text question and 43 has option ids 1 and 3 selected.
            share_hash: Required if submitting via a public link share instead
                of direct access. Obtain from the form's `hash` field.

        Returns:
            Empty OCS data on success (HTTP 201).
        """
        client = get_client()
        body: dict[str, Any] = {"answers": answers}
        if share_hash is not None:
            body["shareHash"] = share_hash
        data = await client.ocs_post_json(
            f"apps/forms/api/v3/forms/{form_id}/submissions",
            json_data=body,
        )
        return '{"status": "submitted"}' if data is None else json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_submission(form_id: int, submission_id: int, answers: dict[str, Any]) -> str:
        """Update an existing submission's answers. Requires allowEditSubmissions on the form.

        Args:
            form_id: Numeric form id.
            submission_id: Numeric submission id. Must belong to the current user.
            answers: Full replacement answers object (same shape as submit_form).

        Returns:
            JSON of the updated submission (refetched).
        """
        client = get_client()
        await client.ocs_put_json(
            f"apps/forms/api/v3/forms/{form_id}/submissions/{submission_id}",
            json_data={"answers": answers},
        )
        data = await client.ocs_get(f"apps/forms/api/v3/forms/{form_id}/submissions/{submission_id}")
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def export_submissions(form_id: int, path: str, file_format: str | None = None) -> str:
        """Export submissions to a spreadsheet file in the user's Nextcloud storage.

        Args:
            form_id: Numeric form id.
            path: Destination folder path inside the user's files (no trailing
                slash). The generated file is created inside this folder.
            file_format: Optional format override: "csv", "xlsx", "ods".
                Defaults to the form's configured fileFormat.

        Returns:
            JSON with the created file's name.
        """
        client = get_client()
        body: dict[str, Any] = {"path": path}
        if file_format is not None:
            body["fileFormat"] = file_format
        data = await client.ocs_post_json(
            f"apps/forms/api/v3/forms/{form_id}/submissions/export",
            json_data=body,
        )
        return json.dumps(data)


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_form(form_id: int) -> str:
        """Delete a form, including all questions, options, shares, and submissions.

        Args:
            form_id: Numeric form id.

        Returns:
            Confirmation with the deleted id.
        """
        client = get_client()
        await client.ocs_delete(f"apps/forms/api/v3/forms/{form_id}")
        return json.dumps({"deleted_form_id": form_id})

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_question(form_id: int, question_id: int) -> str:
        """Delete a question and its options from a form.

        Args:
            form_id: Numeric form id.
            question_id: Numeric question id.

        Returns:
            Confirmation with the deleted id.
        """
        client = get_client()
        await client.ocs_delete(f"apps/forms/api/v3/forms/{form_id}/questions/{question_id}")
        return json.dumps({"deleted_question_id": question_id})

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_option(form_id: int, question_id: int, option_id: int) -> str:
        """Delete an answer option from a question.

        Args:
            form_id: Numeric form id.
            question_id: Numeric question id.
            option_id: Numeric option id.

        Returns:
            Confirmation with the deleted id.
        """
        client = get_client()
        await client.ocs_delete(f"apps/forms/api/v3/forms/{form_id}/questions/{question_id}/options/{option_id}")
        return json.dumps({"deleted_option_id": option_id})

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_form_share(form_id: int, share_id: int) -> str:
        """Revoke a share on a form.

        Args:
            form_id: Numeric form id.
            share_id: Numeric share id.

        Returns:
            Confirmation with the deleted id.
        """
        client = get_client()
        await client.ocs_delete(f"apps/forms/api/v3/forms/{form_id}/shares/{share_id}")
        return json.dumps({"deleted_share_id": share_id})

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_submission(form_id: int, submission_id: int) -> str:
        """Delete a single submission.

        Args:
            form_id: Numeric form id.
            submission_id: Numeric submission id.

        Returns:
            Confirmation with the deleted id.
        """
        client = get_client()
        await client.ocs_delete(f"apps/forms/api/v3/forms/{form_id}/submissions/{submission_id}")
        return json.dumps({"deleted_submission_id": submission_id})

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_all_submissions(form_id: int) -> str:
        """Delete every submission on a form. Does not delete the form itself.

        Args:
            form_id: Numeric form id.

        Returns:
            Confirmation.
        """
        client = get_client()
        await client.ocs_delete(f"apps/forms/api/v3/forms/{form_id}/submissions")
        return json.dumps({"cleared_form_id": form_id})


def register(mcp: FastMCP) -> None:
    """Register Forms tools with the MCP server."""
    _register_read_tools(mcp)
    _register_question_reads(mcp)
    _register_submission_reads(mcp)
    _register_form_writes(mcp)
    _register_question_writes(mcp)
    _register_option_writes(mcp)
    _register_share_writes(mcp)
    _register_submission_writes(mcp)
    _register_destructive_tools(mcp)
