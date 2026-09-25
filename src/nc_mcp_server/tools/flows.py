"""Flow tools: list, create, update and delete Nextcloud Flow rules via the workflowengine OCS API."""

import base64
import binascii
import html
import json
import re
from typing import Any, cast

from mcp.server.fastmcp import FastMCP

from ..annotations import DESTRUCTIVE, DESTRUCTIVE_NON_IDEMPOTENT, READONLY
from ..client import NextcloudError
from ..permissions import (
    PermissionDeniedError,
    PermissionLevel,
    get_permission_level,
    require_permission,
)
from ..state import get_client

API = "apps/workflowengine/api/v1/workflows"
FILE_ENTITY = "OCA\\WorkflowEngine\\Entity\\File"

# Scope name -> the settings page that lists what can be used in that scope
_SCOPES = {"user": "settings/user/workflow", "global": "settings/admin/workflow"}

# Operators and value formats of the checks Nextcloud itself ships. The settings page lists the checks
# but leaves these to its JavaScript, so they are spelled out here; checks added by apps are not covered.
_CHECK_GUIDE: dict[str, dict[str, Any]] = {
    "OCA\\WorkflowEngine\\Check\\FileMimeType": {
        "operators": ["is", "!is", "matches", "!matches"],
        "value": 'A MIME type such as "application/pdf"; with matches, a regular expression such as "/^image\\//"',
    },
    "OCA\\WorkflowEngine\\Check\\FileName": {
        "operators": ["is", "!is", "matches", "!matches"],
        "value": (
            'A file name such as "report.pdf" (is and !is ignore case); with matches, a regular expression'
            ' such as "/\\.pdf$/i"'
        ),
    },
    "OCA\\WorkflowEngine\\Check\\FileSize": {
        "operators": ["less", "!less", "greater", "!greater"],
        "value": (
            'A whole number with a unit, such as "10 MB", "512 KB" or "2048 B" (1024-based, no decimals).'
            " The size is read from the upload request, so this only matches uploads"
        ),
    },
    "OCA\\WorkflowEngine\\Check\\FileSystemTags": {
        "operators": ["is", "!is"],
        "value": "A system tag ID (see list_tags)",
    },
    "OCA\\WorkflowEngine\\Check\\RequestRemoteAddress": {
        "operators": ["matchesIPv4", "!matchesIPv4", "matchesIPv6", "!matchesIPv6"],
        "value": 'An address range in CIDR notation such as "192.168.0.0/16" or "2001:db8::/32"',
    },
    "OCA\\WorkflowEngine\\Check\\RequestTime": {
        "operators": ["in", "!in"],
        "value": (
            "A JSON list of start and end time, each with an Area/Location time zone, written exactly like"
            ' ["08:00 Europe/Berlin","17:00 Europe/Berlin"] (no space after the comma; for UTC use "Etc/UTC")'
        ),
    },
    "OCA\\WorkflowEngine\\Check\\RequestURL": {
        "operators": ["is", "!is", "matches", "!matches"],
        "value": 'A URL; with matches, a regular expression; with is or !is, "webdav" means any WebDAV request',
    },
    "OCA\\WorkflowEngine\\Check\\RequestUserAgent": {
        "operators": ["is", "!is", "matches", "!matches"],
        "value": (
            'A user agent; with matches, a regular expression; with is or !is, also "android", "ios",'
            ' "desktop" (the sync client) or "mail" (Outlook and Thunderbird add-ons)'
        ),
    },
    "OCA\\WorkflowEngine\\Check\\UserGroupMembership": {
        "operators": ["is", "!is"],
        "value": "A group ID (see list_groups)",
    },
}

# An agent must never be a way to run commands of its choosing on the Nextcloud server, so create_flow and
# update_flow refuse, whatever the permission level, workflow_script's operation (it runs the command the
# rule holds) and workflow_ocr's with custom command-line arguments (passed to ocrmypdf with no allow-list;
# some load code). Compared in lower case: PHP resolves class names without regard to case.
_COMMAND_OPERATIONS = {"oca\\workflowscript\\operation"}
_OCR_OPERATION = "oca\\workflowocr\\operation"

_INPUT_TAG = re.compile(r"<input\b[^>]*>")
_ID_ATTR = re.compile(r'\bid="initial-state-workflowengine-([a-z-]+)"')
_VALUE_ATTR = re.compile(r'\bvalue="([^"]*)"')


def _scope(scope: str) -> str:
    if scope not in _SCOPES:
        raise ValueError(f"Invalid scope '{scope}'. Must be one of: {', '.join(_SCOPES)}")
    return scope


def _require_level_for(scope: str, tool: str) -> None:
    """Global flows act on every user's files, so changing them needs the destructive level."""
    current = get_permission_level()
    if scope == "global" and not current.includes(PermissionLevel.DESTRUCTIVE):
        raise PermissionDeniedError(f"{tool} for global flows", PermissionLevel.DESTRUCTIVE, current)


def _refuse_command_operation(operation_class: str, config: str) -> None:
    normalized = operation_class.strip().lstrip("\\").lower()
    if normalized in _COMMAND_OPERATIONS:
        raise ValueError(
            f"{operation_class} runs commands on the Nextcloud server; this server does not create or change"
            " such flows."
        )
    if normalized == _OCR_OPERATION:
        try:
            settings: Any = json.loads(config) if config else None
        except ValueError:
            return  # not JSON; Nextcloud rejects it
        if isinstance(settings, dict) and str(cast(dict[str, Any], settings).get("customCliArgs") or "").strip():
            raise ValueError(
                "Custom command-line arguments for the OCR operation reach ocrmypdf unchecked; this server does"
                " not set them. Leave customCliArgs empty."
            )


def _format_flow(flow: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": flow.get("id"),
        "name": flow.get("name", ""),
        "operation_class": flow.get("class", ""),
        "operation_config": flow.get("operation", ""),
        "entity": flow.get("entity", ""),
        "events": flow.get("events", []),
        "checks": [
            {"class": c.get("class", ""), "operator": c.get("operator", ""), "value": c.get("value", "")}
            for c in flow.get("checks", [])
        ],
    }


def _explain(e: NextcloudError, scope: str, flow_id: int | None = None) -> NextcloudError:
    """Turn the workflow engine's least helpful answers into errors an agent can act on."""
    if e.status_code == 500:
        return NextcloudError(
            f"{e}. An unknown operation, check or entity class makes Nextcloud fail this way;"
            " get_flow_options lists the ones available",
            500,
        )
    if e.status_code == 403 and flow_id is not None and "not within scope" in str(e):
        return NextcloudError(f"No flow with ID {flow_id} in the {scope} scope (see list_flows)", 404)
    if e.status_code == 403 and scope == "user" and "User not logged in" in str(e):
        # What the user endpoints answer when an admin has turned off user flows
        return NextcloudError(f"{e}. User flows are probably disabled on this instance", 403)
    return e


def _config_string(config: str | dict[str, Any]) -> str:
    """Operation settings are a string to Nextcloud, but a JSON one often arrives already parsed.

    FastMCP decodes JSON-looking strings for any parameter not typed plain str, and agents may pass an
    object directly, so both forms are accepted and objects are written back compactly.
    """
    return config if isinstance(config, str) else json.dumps(config, separators=(",", ":"))


def _validate_checks(checks: list[dict[str, str]]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for check in checks:
        missing = [key for key in ("class", "operator", "value") if key not in check]
        if missing:
            raise ValueError(f"Every check needs class, operator and value; {check} lacks {', '.join(missing)}.")
        cleaned.append({"class": check["class"], "operator": check["operator"], "value": check["value"]})
    if not cleaned:
        raise ValueError("Nextcloud needs at least one check per flow.")
    return cleaned


async def _flows(scope: str) -> list[dict[str, Any]]:
    try:
        data = await get_client().ocs_get(f"{API}/{scope}")
    except NextcloudError as e:
        raise _explain(e, scope) from e
    # Flows come grouped by operation class; with none at all Nextcloud sends an empty list instead
    groups: list[list[dict[str, Any]]] = []
    if isinstance(data, dict):
        groups = list(cast(dict[str, list[dict[str, Any]]], data).values())
    flows = [_format_flow(flow) for group in groups for flow in group]
    return sorted(flows, key=lambda f: int(f["id"] or 0))


def _initial_state(page: str) -> dict[str, Any]:
    """Read the workflowengine initial state the Flow settings page embeds for its JavaScript."""
    state: dict[str, Any] = {}
    for tag in _INPUT_TAG.findall(page):
        key, value = _ID_ATTR.search(tag), _VALUE_ATTR.search(tag)
        if key and value:
            try:
                state[key.group(1)] = json.loads(base64.b64decode(html.unescape(value.group(1))))
            except (binascii.Error, ValueError):
                continue
    return state


def _as_list(value: Any) -> list[Any]:
    """The page encodes some lists as JSON objects keyed by index or class; take the values either way."""
    if isinstance(value, dict):
        return list(value.values())  # type: ignore[arg-type]
    return list(value) if isinstance(value, list) else []  # type: ignore[arg-type]


def _format_options(state: dict[str, Any], scope: str) -> dict[str, Any]:
    operations = [
        {
            "class": op.get("id", ""),
            "name": op.get("name", ""),
            "description": op.get("description", ""),
            "entity": op.get("fixedEntity") or None,
            "events_fixed": bool(op.get("isComplex")),
            "trigger": op.get("triggerHint") or None,
        }
        for op in _as_list(state.get("operators"))
    ]
    entities = [
        {
            "class": entity.get("id", ""),
            "name": entity.get("name", ""),
            "events": [
                {"event": e.get("eventName", ""), "name": e.get("displayName", "")} for e in entity.get("events", [])
            ],
        }
        for entity in _as_list(state.get("entities"))
    ]
    checks = [
        {
            "class": check.get("id", ""),
            "entities": check.get("supportedEntities", []),
            **_CHECK_GUIDE.get(check.get("id", ""), {}),
        }
        for check in _as_list(state.get("checks"))
    ]
    return {"scope": scope, "operations": operations, "entities": entities, "checks": checks}


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_flows(scope: str = "user", limit: int = 50, offset: int = 0) -> str:
        """List Nextcloud Flow rules (workflows that act on files automatically).

        Args:
            scope: "user" for the current user's own flows, or "global" for the
                admin flows that apply to everyone (admin only).
            limit: Maximum number of flows to return (1-200, default 50).
            offset: Number of flows to skip for pagination (default 0).

        Returns:
            JSON with "data" (flows with id, name, operation_class,
            operation_config, entity, events and checks, each check having
            class, operator and value) and "pagination" (count, offset, limit,
            has_more).
        """
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        flows = await _flows(_scope(scope))
        page = flows[offset : offset + limit]
        return json.dumps(
            {
                "data": page,
                "pagination": {
                    "count": len(page),
                    "offset": offset,
                    "limit": limit,
                    "has_more": offset + limit < len(flows),
                },
            },
            default=str,
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_flow_options(scope: str = "user") -> str:
        """List what a Flow rule can be built from in a scope: operations, entities, events and checks.

        Read this before create_flow: the available operations depend on the
        installed apps (Talk's "Write to conversation", for one, or core's "Block
        file versioning" for global flows), and some only exist in one scope.

        Args:
            scope: "user" or "global" (admin only).

        Returns:
            JSON with "operations" (class, name, description, entity: the entity
            the operation is bound to or null, events_fixed: true when the
            operation decides its own events and create_flow takes none,
            trigger), "entities" (class, name, events with event and name) and
            "checks" (class, entities it applies to, empty meaning any; for the
            checks Nextcloud ships also operators and a value description).
        """
        page = await get_client().web_page(_SCOPES[_scope(scope)])
        state = _initial_state(page)
        if "operators" not in state:
            raise NextcloudError(
                "The Flow settings page did not contain the list of operations; user flows may be disabled"
                " on this instance, or the page layout changed",
                502,
            )
        return json.dumps(_format_options(state, scope), default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE_NON_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def create_flow(
        operation_class: str,
        checks: list[dict[str, str]],
        scope: str = "user",
        name: str = "",
        operation_config: str | dict[str, Any] = "",
        entity: str = FILE_ENTITY,
        events: list[str] | None = None,
    ) -> str:
        """Create a Nextcloud Flow rule: when an event happens and all checks match, run an operation.

        Use get_flow_options for the operation, entity, event and check class names
        of the scope; an unknown class name makes Nextcloud fail with a server error.
        User flows act on the current user's files only. Global flows act on
        everyone's and need the destructive permission level: a global access
        control rule whose checks match too much locks every user, this server
        included, out of their files. Operations that would run a command, or
        command-line arguments, of the agent's choosing on the server are refused.

        Args:
            operation_class: Class of the operation to run, from get_flow_options.
                Example: "OCA\\Talk\\Flow\\Operation"
            checks: Conditions that must all match, at least one. Each is an object
                with "class", "operator" and "value", e.g. {"class":
                "OCA\\WorkflowEngine\\Check\\FileName", "operator": "matches",
                "value": "/\\.pdf$/i"}. get_flow_options lists operators and value
                formats for the built-in checks.
            scope: "user" (default) or "global" (admin only).
            name: Optional name for the rule.
            operation_config: The operation's own settings; the format depends on
                the operation. Talk's "Write to conversation" takes
                {"m": 1, "t": "<conversation token>"} (m: 1 no mention, 2 mention
                yourself, 3 mention everyone in the room, moderators only), as an
                object or a JSON string. Empty for operations without settings.
            entity: What the rule acts on. Defaults to files
                ("OCA\\WorkflowEngine\\Entity\\File"), the only entity Nextcloud
                ships.
            events: Entity events that trigger the rule, e.g.
                ["\\OCP\\Files::postCreate"]. Required unless the operation has
                events_fixed in get_flow_options, in which case leave it empty.

        Returns:
            JSON with the created flow, as list_flows shows it.
        """
        scope = _scope(scope)
        _require_level_for(scope, "create_flow")
        _refuse_command_operation(operation_class, _config_string(operation_config))
        body = {
            "class": operation_class,
            "name": name,
            "checks": _validate_checks(checks),
            "operation": _config_string(operation_config),
            "entity": entity,
            "events": events or [],
        }
        try:
            data = await get_client().ocs_post_json(f"{API}/{scope}", json_data=body)
        except NextcloudError as e:
            raise _explain(e, scope) from e
        return json.dumps(_format_flow(data), default=str)

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.WRITE)
    async def update_flow(
        flow_id: int,
        scope: str = "user",
        name: str | None = None,
        checks: list[dict[str, str]] | None = None,
        operation_config: str | dict[str, Any] | None = None,
        entity: str | None = None,
        events: list[str] | None = None,
    ) -> str:
        """Change a Nextcloud Flow rule. Only the fields you pass change; the operation class cannot.

        Global flows need the destructive permission level.

        Args:
            flow_id: The flow's ID, from list_flows.
            scope: The scope the flow is in: "user" (default) or "global".
            name: New name.
            checks: New complete list of checks (replaces the old ones), each with
                "class", "operator" and "value"; at least one.
            operation_config: New operation settings, an object or a JSON string
                (see create_flow).
            entity: New entity class.
            events: New complete list of triggering events.

        Returns:
            JSON with the updated flow, as list_flows shows it.
        """
        scope = _scope(scope)
        _require_level_for(scope, "update_flow")
        current = next((f for f in await _flows(scope) if f["id"] == flow_id), None)
        if current is None:
            raise ValueError(f"No flow with ID {flow_id} in the {scope} scope (see list_flows).")
        body = {
            "name": current["name"] if name is None else name,
            "checks": current["checks"] if checks is None else _validate_checks(checks),
            "operation": current["operation_config"] if operation_config is None else _config_string(operation_config),
            "entity": current["entity"] if entity is None else entity,
            "events": current["events"] if events is None else events,
        }
        _refuse_command_operation(current["operation_class"], str(body["operation"]))
        try:
            data = await get_client().ocs_put_json(f"{API}/{scope}/{flow_id}", json_data=body)
        except NextcloudError as e:
            raise _explain(e, scope, flow_id) from e
        return json.dumps(_format_flow(data), default=str)


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_flow(flow_id: int, scope: str = "user") -> str:
        """Delete a Nextcloud Flow rule.

        Args:
            flow_id: The flow's ID, from list_flows.
            scope: The scope the flow is in: "user" (default) or "global".

        Returns:
            Confirmation message.
        """
        scope = _scope(scope)
        try:
            await get_client().ocs_delete(f"{API}/{scope}/{flow_id}")
        except NextcloudError as e:
            raise _explain(e, scope, flow_id) from e
        return f"Flow {flow_id} deleted."


def register(mcp: FastMCP) -> None:
    """Register Flow tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
