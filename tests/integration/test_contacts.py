"""Integration tests for Contacts tools against a real Nextcloud instance."""

import contextlib
import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config
from nc_mcp_server.state import get_config
from nc_mcp_server.tools.contacts import CONTACTS_REPORT, _format_contact, _parse_report_xml

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

BOOK_ID = "contacts"
PREFIX = "mcp-test-contact"


async def _delete_mcp_contacts(client: NextcloudClient, user: str) -> None:
    """Delete all mcp-* contacts via direct DAV calls (bypasses MCP permission state)."""
    try:
        response = await client.dav_request(
            "REPORT",
            f"addressbooks/users/{user}/{BOOK_ID}/",
            body=CONTACTS_REPORT,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context="Cleanup test contacts",
        )
    except Exception:  # noqa: BLE001
        return
    for href, _etag, vcard_data in _parse_report_xml(response.text or ""):
        with contextlib.suppress(Exception):
            contact = _format_contact(vcard_data)
            if contact["uid"].startswith("mcp-"):
                resource = href.split(f"/{BOOK_ID}/", 1)[1] if f"/{BOOK_ID}/" in href else f"{contact['uid']}.vcf"
                await client.dav_request(
                    "DELETE",
                    f"addressbooks/users/{user}/{BOOK_ID}/{resource}",
                    context=f"Cleanup '{contact['uid']}'",
                )


@pytest.fixture(autouse=True)
async def _cleanup_test_contacts(_cleanup_config: Config) -> None:
    """Delete any leftover test contacts before each test.

    Uses a standalone DAV client instead of nc_mcp to avoid mutating the global
    permission state that permission-specific fixtures (nc_mcp_read_only, etc.) rely on.
    """
    client = NextcloudClient(_cleanup_config)
    try:
        await _delete_mcp_contacts(client, _cleanup_config.user)
    finally:
        await client.close()


async def _create(nc_mcp: McpTestHelper, suffix: str, **extra: str) -> dict[str, Any]:
    """Create a test contact and return the parsed result."""
    kw: dict[str, str] = {"full_name": f"{PREFIX}-{suffix}", "book_id": BOOK_ID, **extra}
    result: dict[str, Any] = json.loads(await nc_mcp.call("create_contact", **kw))
    return result


class TestListAddressbooks:
    @pytest.mark.asyncio
    async def test_returns_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_addressbooks")
        books: list[dict[str, Any]] = json.loads(result)
        assert isinstance(books, list)
        assert len(books) >= 1

    @pytest.mark.asyncio
    async def test_default_contacts_book_exists(self, nc_mcp: McpTestHelper) -> None:
        books = json.loads(await nc_mcp.call("list_addressbooks"))
        ids = [b["id"] for b in books]
        assert "contacts" in ids

    @pytest.mark.asyncio
    async def test_book_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        books = json.loads(await nc_mcp.call("list_addressbooks"))
        book = books[0]
        for field in ["id", "name"]:
            assert field in book, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_excludes_system_books(self, nc_mcp: McpTestHelper) -> None:
        books = json.loads(await nc_mcp.call("list_addressbooks"))
        ids = [b["id"] for b in books]
        assert "z-server-generated--system" not in ids
        assert "z-app-generated--contactsinteraction--recent" not in ids


class TestCreateContact:
    @pytest.mark.asyncio
    async def test_create_with_full_name(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "fullname")
        assert contact["uid"].startswith("mcp-")
        assert contact["full_name"] == f"{PREFIX}-fullname"

    @pytest.mark.asyncio
    async def test_create_with_given_and_family(self, nc_mcp: McpTestHelper) -> None:
        result = json.loads(
            await nc_mcp.call("create_contact", given_name=f"{PREFIX}-given", family_name="Family", book_id=BOOK_ID)
        )
        assert "given" in result.get("full_name", "").lower() or result.get("name", {}).get("given")

    @pytest.mark.asyncio
    async def test_create_with_email(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "email", email="test@example.com")
        assert any(e["value"] == "test@example.com" for e in contact.get("emails", []))

    @pytest.mark.asyncio
    async def test_create_with_phone(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "phone", phone="+1234567890")
        assert any(p["value"] == "+1234567890" for p in contact.get("phones", []))

    @pytest.mark.asyncio
    async def test_create_with_organization(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "org", organization="Test Corp")
        assert contact.get("organization") == "Test Corp"

    @pytest.mark.asyncio
    async def test_create_with_title(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "title", title="Engineer")
        assert contact.get("title") == "Engineer"

    @pytest.mark.asyncio
    async def test_create_with_note(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "note", note="Important person")
        assert contact.get("note") == "Important person"

    @pytest.mark.asyncio
    async def test_create_with_all_fields(self, nc_mcp: McpTestHelper) -> None:
        contact = json.loads(
            await nc_mcp.call(
                "create_contact",
                full_name=f"{PREFIX}-allflds",
                email="all@test.com",
                phone="+9999999999",
                organization="Full Corp",
                title="CTO",
                note="Has all fields",
                book_id=BOOK_ID,
            )
        )
        assert contact["full_name"] == f"{PREFIX}-allflds"
        assert contact.get("organization") == "Full Corp"
        assert contact.get("title") == "CTO"
        assert contact.get("note") == "Has all fields"

    @pytest.mark.asyncio
    async def test_create_special_chars_roundtrip(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "special-R&D", organization="R&D <Team>", title='VP "Sales"')
        fetched = json.loads(await nc_mcp.call("get_contact", uid=contact["uid"], book_id=BOOK_ID))
        assert fetched.get("organization") == "R&D <Team>"
        assert fetched.get("title") == 'VP "Sales"'

    @pytest.mark.asyncio
    async def test_create_note_with_crlf(self, nc_mcp: McpTestHelper) -> None:
        """Notes with Windows line endings must roundtrip without bare \\r corruption."""
        contact = await _create(nc_mcp, "crlf-note", note="Line1\r\nLine2\rLine3\nLine4")
        fetched = json.loads(await nc_mcp.call("get_contact", uid=contact["uid"], book_id=BOOK_ID))
        note = fetched.get("note", "")
        assert "Line1" in note
        assert "Line2" in note
        assert "Line3" in note
        assert "Line4" in note
        assert "\r" not in note

    @pytest.mark.asyncio
    async def test_create_no_name_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("create_contact", email="noname@test.com", book_id=BOOK_ID)

    @pytest.mark.asyncio
    async def test_create_unicode_name(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "unicode-Müller-日本語")
        assert "Müller" in contact["full_name"]
        assert "日本語" in contact["full_name"]


class TestGetContacts:
    @pytest.mark.asyncio
    async def test_returns_paginated_response(self, nc_mcp: McpTestHelper) -> None:
        result = json.loads(await nc_mcp.call("get_contacts", book_id=BOOK_ID, limit=200))
        assert "data" in result
        assert "pagination" in result
        assert isinstance(result["data"], list)

    @pytest.mark.asyncio
    async def test_created_contact_appears(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "appears")
        result = json.loads(await nc_mcp.call("get_contacts", book_id=BOOK_ID, limit=200))
        uids = [c["uid"] for c in result["data"]]
        assert created["uid"] in uids

    @pytest.mark.asyncio
    async def test_contact_has_etag(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "etag")
        result = json.loads(await nc_mcp.call("get_contacts", book_id=BOOK_ID, limit=200))
        match = next(c for c in result["data"] if c["uid"] == created["uid"])
        assert match["etag"]

    @pytest.mark.asyncio
    async def test_pagination_limit(self, nc_mcp: McpTestHelper) -> None:
        for i in range(3):
            await _create(nc_mcp, f"paglim-{i}")
        result = json.loads(await nc_mcp.call("get_contacts", book_id=BOOK_ID, limit=2))
        assert result["pagination"]["count"] <= 2
        assert result["pagination"]["limit"] == 2

    @pytest.mark.asyncio
    async def test_nonexistent_book_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_contacts", book_id="nonexistent-book-xyz")


class TestGetContact:
    @pytest.mark.asyncio
    async def test_get_by_uid(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "getbyuid", email="get@test.com")
        contact = json.loads(await nc_mcp.call("get_contact", uid=created["uid"], book_id=BOOK_ID))
        assert contact["uid"] == created["uid"]
        assert contact["full_name"] == f"{PREFIX}-getbyuid"
        assert any(e["value"] == "get@test.com" for e in contact.get("emails", []))
        assert contact["etag"]

    @pytest.mark.asyncio
    async def test_nonexistent_uid_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("get_contact", uid="nonexistent-uid-xyz", book_id=BOOK_ID)


class TestUpdateContact:
    @pytest.mark.asyncio
    async def test_update_title(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "upd-title")
        contact = json.loads(await nc_mcp.call("get_contact", uid=created["uid"], book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call(
                "update_contact", uid=created["uid"], etag=contact["etag"], title="New Title", book_id=BOOK_ID
            )
        )
        assert updated.get("title") == "New Title"

    @pytest.mark.asyncio
    async def test_update_email(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "upd-email", email="old@test.com")
        contact = json.loads(await nc_mcp.call("get_contact", uid=created["uid"], book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call(
                "update_contact", uid=created["uid"], etag=contact["etag"], email="new@test.com", book_id=BOOK_ID
            )
        )
        emails = [e["value"] for e in updated.get("emails", [])]
        assert "new@test.com" in emails

    @pytest.mark.asyncio
    async def test_update_organization(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "upd-org", organization="Old Corp")
        contact = json.loads(await nc_mcp.call("get_contact", uid=created["uid"], book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call(
                "update_contact",
                uid=created["uid"],
                etag=contact["etag"],
                organization="New Corp",
                book_id=BOOK_ID,
            )
        )
        assert updated.get("organization") == "New Corp"

    @pytest.mark.asyncio
    async def test_update_preserves_unchanged_fields(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "upd-preserve", email="keep@test.com", organization="Keep Corp")
        contact = json.loads(await nc_mcp.call("get_contact", uid=created["uid"], book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call(
                "update_contact", uid=created["uid"], etag=contact["etag"], note="Added note", book_id=BOOK_ID
            )
        )
        assert updated.get("note") == "Added note"
        assert updated.get("organization") == "Keep Corp"

    @pytest.mark.asyncio
    async def test_update_etag_changes(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "upd-etag")
        contact = json.loads(await nc_mcp.call("get_contact", uid=created["uid"], book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call(
                "update_contact", uid=created["uid"], etag=contact["etag"], title="Changed", book_id=BOOK_ID
            )
        )
        assert updated["etag"] != contact["etag"]

    @pytest.mark.asyncio
    async def test_update_wrong_etag_fails(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "upd-badetag")
        with pytest.raises(ToolError):
            await nc_mcp.call("update_contact", uid=created["uid"], etag="wrong-etag", title="Nope", book_id=BOOK_ID)

    @pytest.mark.asyncio
    async def test_update_clear_note(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "upd-clrnote", note="Will be cleared")
        contact = json.loads(await nc_mcp.call("get_contact", uid=created["uid"], book_id=BOOK_ID))
        assert contact.get("note") == "Will be cleared"
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=created["uid"], etag=contact["etag"], note="", book_id=BOOK_ID)
        )
        assert "note" not in updated

    @pytest.mark.asyncio
    async def test_update_clear_title_preserves_others(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "upd-clrtitle", title="Old Title", organization="Keep Corp")
        contact = json.loads(await nc_mcp.call("get_contact", uid=created["uid"], book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=created["uid"], etag=contact["etag"], title="", book_id=BOOK_ID)
        )
        assert "title" not in updated
        assert updated.get("organization") == "Keep Corp"

    @pytest.mark.asyncio
    async def test_update_no_fields_raises(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "upd-nofields")
        contact = json.loads(await nc_mcp.call("get_contact", uid=created["uid"], book_id=BOOK_ID))
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("update_contact", uid=created["uid"], etag=contact["etag"], book_id=BOOK_ID)

    @pytest.mark.asyncio
    async def test_update_nonexistent_uid_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("update_contact", uid="nonexistent-uid-xyz", etag="fake", title="Nope", book_id=BOOK_ID)

    @pytest.mark.asyncio
    async def test_update_folded_note(self, nc_mcp: McpTestHelper) -> None:
        """Updating a contact whose NOTE was folded by the server must not corrupt other fields."""
        uid = "mcp-fold-note"
        long_note = "A" * 100
        full_name = f"{PREFIX}-fold-note"
        lines = [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"UID:{uid}",
            f"FN:{full_name}",
            f"N:;{full_name};;;",
            f"NOTE:{long_note}",
            "ORG:Keep Corp",
            "END:VCARD",
        ]
        vcard = "\r\n".join(lines) + "\r\n"
        config = get_config()
        await nc_mcp.client.dav_request(
            "PUT",
            f"addressbooks/users/{config.user}/{BOOK_ID}/{uid}.vcf",
            body=vcard,
            headers={"Content-Type": "text/vcard; charset=utf-8"},
            context=f"Create test contact '{uid}'",
        )
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        assert contact.get("organization") == "Keep Corp"
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], note="Short note", book_id=BOOK_ID)
        )
        assert updated.get("note") == "Short note"
        assert updated.get("organization") == "Keep Corp"

    @pytest.mark.asyncio
    async def test_update_clear_full_name_keeps_fn_from_n(self, nc_mcp: McpTestHelper) -> None:
        """Clearing full_name must not produce a vCard without FN — synthesize from N."""
        uid = await _put_vcard_with_name(nc_mcp, "clr-fn", given="John", family="Doe")
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], full_name="", book_id=BOOK_ID)
        )
        assert updated["full_name"], "FN must not be empty after clearing full_name"
        assert "John" in updated["full_name"]
        assert "Doe" in updated["full_name"]

    @pytest.mark.asyncio
    async def test_update_clear_full_name_without_n(self, nc_mcp: McpTestHelper) -> None:
        """Clearing full_name on a contact with only FN (no structured N) keeps old FN."""
        created = await _create(nc_mcp, "clr-fn-only")
        contact = json.loads(await nc_mcp.call("get_contact", uid=created["uid"], book_id=BOOK_ID))
        original_fn = contact["full_name"]
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=created["uid"], etag=contact["etag"], full_name="", book_id=BOOK_ID)
        )
        assert updated["full_name"], "FN must not be empty"
        assert PREFIX in updated["full_name"] or updated["full_name"] == original_fn

    @pytest.mark.asyncio
    async def test_update_clear_both_names_keeps_fn(self, nc_mcp: McpTestHelper) -> None:
        """Clearing both given_name and family_name must still produce a non-empty FN."""
        uid = await _put_vcard_with_name(nc_mcp, "clr-both", given="John", family="Doe")
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call(
                "update_contact",
                uid=uid,
                etag=contact["etag"],
                given_name="",
                family_name="",
                book_id=BOOK_ID,
            )
        )
        assert updated["full_name"], "FN must not be empty when both name parts are cleared"


async def _put_vcard_with_categories(nc_mcp: McpTestHelper, suffix: str, categories: list[str]) -> str:
    """Create a test contact with CATEGORIES via direct CardDAV PUT. Returns UID."""
    uid = f"mcp-cat-{suffix}"
    full_name = f"{PREFIX}-cat-{suffix}"
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"UID:{uid}",
        f"FN:{full_name}",
        f"N:;{full_name};;;",
    ]
    if categories:
        lines.append(f"CATEGORIES:{','.join(categories)}")
    lines.append("END:VCARD")
    vcard = "\r\n".join(lines) + "\r\n"
    config = get_config()
    await nc_mcp.client.dav_request(
        "PUT",
        f"addressbooks/users/{config.user}/{BOOK_ID}/{uid}.vcf",
        body=vcard,
        headers={"Content-Type": "text/vcard; charset=utf-8"},
        context=f"Create test contact '{uid}'",
    )
    return uid


class TestMultiValueEmailPhone:
    @pytest.mark.asyncio
    async def test_create_with_multiple_emails(self, nc_mcp: McpTestHelper) -> None:
        emails = [{"value": "work@test.com", "type": "WORK"}, {"value": "home@test.com", "type": "HOME"}]
        contact = json.loads(
            await nc_mcp.call("create_contact", full_name=f"{PREFIX}-multi-email", emails=emails, book_id=BOOK_ID)
        )
        values = {e["value"] for e in contact.get("emails", [])}
        assert values == {"work@test.com", "home@test.com"}

    @pytest.mark.asyncio
    async def test_create_with_multiple_phones(self, nc_mcp: McpTestHelper) -> None:
        phones = [{"value": "+1111", "type": "CELL"}, {"value": "+2222", "type": "WORK"}]
        contact = json.loads(
            await nc_mcp.call("create_contact", full_name=f"{PREFIX}-multi-phone", phones=phones, book_id=BOOK_ID)
        )
        values = {p["value"] for p in contact.get("phones", [])}
        assert values == {"+1111", "+2222"}

    @pytest.mark.asyncio
    async def test_create_preserves_email_types(self, nc_mcp: McpTestHelper) -> None:
        emails = [{"value": "w@test.com", "type": "WORK"}, {"value": "h@test.com", "type": "HOME"}]
        contact = json.loads(
            await nc_mcp.call("create_contact", full_name=f"{PREFIX}-email-types", emails=emails, book_id=BOOK_ID)
        )
        type_map = {e["value"]: e.get("type", "") for e in contact.get("emails", [])}
        assert type_map["w@test.com"] == "WORK"
        assert type_map["h@test.com"] == "HOME"

    @pytest.mark.asyncio
    async def test_create_email_and_emails_conflict(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call(
                "create_contact",
                full_name=f"{PREFIX}-conflict",
                email="a@test.com",
                emails=[{"value": "b@test.com", "type": "WORK"}],
                book_id=BOOK_ID,
            )

    @pytest.mark.asyncio
    async def test_create_phone_and_phones_conflict(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call(
                "create_contact",
                full_name=f"{PREFIX}-conflict2",
                phone="+111",
                phones=[{"value": "+222", "type": "CELL"}],
                book_id=BOOK_ID,
            )

    @pytest.mark.asyncio
    async def test_update_with_multiple_emails(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "upd-multi-email", email="old@test.com")
        fetched = json.loads(await nc_mcp.call("get_contact", uid=contact["uid"], book_id=BOOK_ID))
        new_emails = [
            {"value": "work@new.com", "type": "WORK"},
            {"value": "personal@new.com", "type": "HOME"},
        ]
        updated = json.loads(
            await nc_mcp.call(
                "update_contact", uid=contact["uid"], etag=fetched["etag"], emails=new_emails, book_id=BOOK_ID
            )
        )
        values = {e["value"] for e in updated.get("emails", [])}
        assert values == {"work@new.com", "personal@new.com"}
        assert "old@test.com" not in values

    @pytest.mark.asyncio
    async def test_update_preserves_existing_emails_when_not_provided(self, nc_mcp: McpTestHelper) -> None:
        """When neither email nor emails is provided, existing emails should be preserved."""
        uid = "mcp-preserve-emails"
        lines = [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"UID:{uid}",
            f"FN:{PREFIX}-preserve-emails",
            f"N:;{PREFIX}-preserve-emails;;;",
            "EMAIL;TYPE=WORK:keep@test.com",
            "EMAIL;TYPE=HOME:also-keep@test.com",
            "END:VCARD",
        ]
        vcard = "\r\n".join(lines) + "\r\n"
        config = get_config()
        await nc_mcp.client.dav_request(
            "PUT",
            f"addressbooks/users/{config.user}/{BOOK_ID}/{uid}.vcf",
            body=vcard,
            headers={"Content-Type": "text/vcard; charset=utf-8"},
            context=f"Create test contact '{uid}'",
        )
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], title="New Title", book_id=BOOK_ID)
        )
        values = {e["value"] for e in updated.get("emails", [])}
        assert values == {"keep@test.com", "also-keep@test.com"}
        assert updated.get("title") == "New Title"

    @pytest.mark.asyncio
    async def test_update_clear_all_emails(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "upd-clear-emails", email="remove@test.com")
        fetched = json.loads(await nc_mcp.call("get_contact", uid=contact["uid"], book_id=BOOK_ID))
        assert fetched.get("emails")
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=contact["uid"], etag=fetched["etag"], emails=[], book_id=BOOK_ID)
        )
        assert not updated.get("emails")

    @pytest.mark.asyncio
    async def test_update_clear_all_emails_via_empty_email(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "upd-clear-email-str", email="remove@test.com")
        fetched = json.loads(await nc_mcp.call("get_contact", uid=contact["uid"], book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=contact["uid"], etag=fetched["etag"], email="", book_id=BOOK_ID)
        )
        assert not updated.get("emails")

    @pytest.mark.asyncio
    async def test_update_with_multiple_phones(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "upd-multi-phone", phone="+0000")
        fetched = json.loads(await nc_mcp.call("get_contact", uid=contact["uid"], book_id=BOOK_ID))
        new_phones = [{"value": "+1111", "type": "CELL"}, {"value": "+2222", "type": "WORK"}]
        updated = json.loads(
            await nc_mcp.call(
                "update_contact", uid=contact["uid"], etag=fetched["etag"], phones=new_phones, book_id=BOOK_ID
            )
        )
        values = {p["value"] for p in updated.get("phones", [])}
        assert values == {"+1111", "+2222"}

    @pytest.mark.asyncio
    async def test_update_email_and_emails_conflict(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "upd-conflict")
        fetched = json.loads(await nc_mcp.call("get_contact", uid=contact["uid"], book_id=BOOK_ID))
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call(
                "update_contact",
                uid=contact["uid"],
                etag=fetched["etag"],
                email="a@test.com",
                emails=[{"value": "b@test.com", "type": "WORK"}],
                book_id=BOOK_ID,
            )

    @pytest.mark.asyncio
    async def test_roundtrip_multi_email_read_modify_write(self, nc_mcp: McpTestHelper) -> None:
        """Read a contact's emails, add one, write back — the full multi-value workflow."""
        emails: list[dict[str, str]] = [{"value": "first@test.com", "type": "WORK"}]
        contact = json.loads(
            await nc_mcp.call("create_contact", full_name=f"{PREFIX}-roundtrip", emails=emails, book_id=BOOK_ID)
        )
        fetched = json.loads(await nc_mcp.call("get_contact", uid=contact["uid"], book_id=BOOK_ID))
        current_emails = fetched.get("emails", [])
        current_emails.append({"value": "second@test.com", "type": "HOME"})
        updated = json.loads(
            await nc_mcp.call(
                "update_contact",
                uid=contact["uid"],
                etag=fetched["etag"],
                emails=current_emails,
                book_id=BOOK_ID,
            )
        )
        values = {e["value"] for e in updated.get("emails", [])}
        assert values == {"first@test.com", "second@test.com"}

    @pytest.mark.asyncio
    async def test_update_email_with_grouped_properties(self, nc_mcp: McpTestHelper) -> None:
        """Updating emails must strip group-prefixed EMAIL lines (e.g. item1.EMAIL)."""
        uid = "mcp-grouped-email"
        full_name = f"{PREFIX}-grouped-email"
        lines = [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"UID:{uid}",
            f"FN:{full_name}",
            f"N:;{full_name};;;",
            "item1.EMAIL;TYPE=WORK:grouped@test.com",
            "item1.X-ABLabel:Work",
            "END:VCARD",
        ]
        vcard = "\r\n".join(lines) + "\r\n"
        config = get_config()
        await nc_mcp.client.dav_request(
            "PUT",
            f"addressbooks/users/{config.user}/{BOOK_ID}/{uid}.vcf",
            body=vcard,
            headers={"Content-Type": "text/vcard; charset=utf-8"},
            context=f"Create test contact '{uid}'",
        )
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        assert any(e["value"] == "grouped@test.com" for e in contact.get("emails", []))
        new_emails = [{"value": "new@test.com", "type": "HOME"}]
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], emails=new_emails, book_id=BOOK_ID)
        )
        values = [e["value"] for e in updated.get("emails", [])]
        assert "new@test.com" in values
        assert "grouped@test.com" not in values, "Grouped EMAIL property was not stripped"


class TestContactCategories:
    @pytest.mark.asyncio
    async def test_single_category(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_categories(nc_mcp, "single", ["Work"])
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        assert contact["categories"] == ["Work"]

    @pytest.mark.asyncio
    async def test_multiple_categories(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_categories(nc_mcp, "multi", ["Work", "VIP", "Family"])
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        assert set(contact["categories"]) == {"Work", "VIP", "Family"}

    @pytest.mark.asyncio
    async def test_no_categories_omits_field(self, nc_mcp: McpTestHelper) -> None:
        contact = await _create(nc_mcp, "cat-none")
        fetched = json.loads(await nc_mcp.call("get_contact", uid=contact["uid"], book_id=BOOK_ID))
        assert "categories" not in fetched

    @pytest.mark.asyncio
    async def test_categories_in_listing(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_categories(nc_mcp, "list", ["Colleague", "Friend"])
        result = json.loads(await nc_mcp.call("get_contacts", book_id=BOOK_ID, limit=200))
        match = next(c for c in result["data"] if c["uid"] == uid)
        assert set(match["categories"]) == {"Colleague", "Friend"}

    @pytest.mark.asyncio
    async def test_unicode_categories(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_categories(nc_mcp, "unicode", ["Arbeit", "Famille", "友達"])
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        assert set(contact["categories"]) == {"Arbeit", "Famille", "友達"}


async def _put_vcard_with_name(nc_mcp: McpTestHelper, suffix: str, given: str = "", family: str = "") -> str:
    """Create a test contact with a structured N field via direct CardDAV PUT. Returns UID."""
    uid = f"mcp-name-{suffix}"
    full_name = f"{PREFIX}-{suffix}"
    n_parts = f"{family};{given};;;"
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"UID:{uid}",
        f"FN:{full_name}",
        f"N:{n_parts}",
        "END:VCARD",
    ]
    vcard = "\r\n".join(lines) + "\r\n"
    config = get_config()
    await nc_mcp.client.dav_request(
        "PUT",
        f"addressbooks/users/{config.user}/{BOOK_ID}/{uid}.vcf",
        body=vcard,
        headers={"Content-Type": "text/vcard; charset=utf-8"},
        context=f"Create test contact '{uid}'",
    )
    return uid


class TestUpdateStructuredName:
    @pytest.mark.asyncio
    async def test_update_given_preserves_family(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_name(nc_mcp, "upd-given", given="John", family="Doe")
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], given_name="Jane", book_id=BOOK_ID)
        )
        assert updated["name"]["given"] == "Jane"
        assert updated["name"]["family"] == "Doe"

    @pytest.mark.asyncio
    async def test_update_family_preserves_given(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_name(nc_mcp, "upd-family", given="John", family="Doe")
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], family_name="Smith", book_id=BOOK_ID)
        )
        assert updated["name"]["given"] == "John"
        assert updated["name"]["family"] == "Smith"

    @pytest.mark.asyncio
    async def test_update_both_names(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_name(nc_mcp, "upd-both", given="John", family="Doe")
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call(
                "update_contact",
                uid=uid,
                etag=contact["etag"],
                given_name="Jane",
                family_name="Smith",
                book_id=BOOK_ID,
            )
        )
        assert updated["name"]["given"] == "Jane"
        assert updated["name"]["family"] == "Smith"

    @pytest.mark.asyncio
    async def test_update_given_auto_updates_fn(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_name(nc_mcp, "upd-autofn", given="John", family="Doe")
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], given_name="Jane", book_id=BOOK_ID)
        )
        assert "Jane" in updated["full_name"]
        assert "Doe" in updated["full_name"]

    @pytest.mark.asyncio
    async def test_update_given_on_family_only_contact(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_name(nc_mcp, "upd-add-given", family="Doe")
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], given_name="Jane", book_id=BOOK_ID)
        )
        assert updated["name"]["given"] == "Jane"
        assert updated["name"]["family"] == "Doe"

    @pytest.mark.asyncio
    async def test_update_family_on_given_only_contact(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_name(nc_mcp, "upd-add-family", given="John")
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], family_name="Doe", book_id=BOOK_ID)
        )
        assert updated["name"]["given"] == "John"
        assert updated["name"]["family"] == "Doe"

    @pytest.mark.asyncio
    async def test_update_preserves_prefix_suffix(self, nc_mcp: McpTestHelper) -> None:
        uid = "mcp-name-upd-prefix"
        full_name = f"{PREFIX}-upd-prefix"
        lines = [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"UID:{uid}",
            f"FN:{full_name}",
            "N:Doe;John;William;Dr.;Jr.",
            "END:VCARD",
        ]
        vcard = "\r\n".join(lines) + "\r\n"
        config = get_config()
        await nc_mcp.client.dav_request(
            "PUT",
            f"addressbooks/users/{config.user}/{BOOK_ID}/{uid}.vcf",
            body=vcard,
            headers={"Content-Type": "text/vcard; charset=utf-8"},
            context=f"Create test contact '{uid}'",
        )
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        assert contact["name"]["prefix"] == "Dr."
        assert contact["name"]["suffix"] == "Jr."
        assert contact["name"]["additional"] == "William"
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], given_name="Jane", book_id=BOOK_ID)
        )
        assert updated["name"]["given"] == "Jane"
        assert updated["name"]["family"] == "Doe"
        assert updated["name"]["additional"] == "William"
        assert updated["name"]["prefix"] == "Dr."
        assert updated["name"]["suffix"] == "Jr."

    @pytest.mark.asyncio
    async def test_update_full_name_with_given(self, nc_mcp: McpTestHelper) -> None:
        uid = await _put_vcard_with_name(nc_mcp, "upd-fn-given", given="John", family="Doe")
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call(
                "update_contact",
                uid=uid,
                etag=contact["etag"],
                full_name="Dr. Jane Smith",
                given_name="Jane",
                family_name="Smith",
                book_id=BOOK_ID,
            )
        )
        assert updated["full_name"] == "Dr. Jane Smith"
        assert updated["name"]["given"] == "Jane"
        assert updated["name"]["family"] == "Smith"

    @pytest.mark.asyncio
    async def test_update_name_preserves_other_fields(self, nc_mcp: McpTestHelper) -> None:
        uid = "mcp-name-upd-preserve"
        full_name = f"{PREFIX}-upd-preserve"
        lines = [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"UID:{uid}",
            f"FN:{full_name}",
            "N:Doe;John;;;",
            "EMAIL;TYPE=WORK:john@test.com",
            "ORG:Test Corp",
            "CATEGORIES:Work,VIP",
            "END:VCARD",
        ]
        vcard = "\r\n".join(lines) + "\r\n"
        config = get_config()
        await nc_mcp.client.dav_request(
            "PUT",
            f"addressbooks/users/{config.user}/{BOOK_ID}/{uid}.vcf",
            body=vcard,
            headers={"Content-Type": "text/vcard; charset=utf-8"},
            context=f"Create test contact '{uid}'",
        )
        contact = json.loads(await nc_mcp.call("get_contact", uid=uid, book_id=BOOK_ID))
        updated = json.loads(
            await nc_mcp.call("update_contact", uid=uid, etag=contact["etag"], given_name="Jane", book_id=BOOK_ID)
        )
        assert updated["name"]["given"] == "Jane"
        assert updated.get("organization") == "Test Corp"
        assert any(e["value"] == "john@test.com" for e in updated.get("emails", []))
        assert set(updated.get("categories", [])) == {"Work", "VIP"}


class TestDeleteContact:
    @pytest.mark.asyncio
    async def test_delete_removes_contact(self, nc_mcp: McpTestHelper) -> None:
        created = await _create(nc_mcp, "del-remove")
        result = await nc_mcp.call("delete_contact", uid=created["uid"], book_id=BOOK_ID)
        assert "deleted" in result.lower()
        contacts = json.loads(await nc_mcp.call("get_contacts", book_id=BOOK_ID, limit=200))
        uids = [c["uid"] for c in contacts["data"]]
        assert created["uid"] not in uids

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("delete_contact", uid="nonexistent-uid-xyz", book_id=BOOK_ID)


class TestContactPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list_addressbooks(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_addressbooks")
        assert isinstance(json.loads(result), list)

    @pytest.mark.asyncio
    async def test_read_only_allows_get_contacts(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("get_contacts", book_id=BOOK_ID, limit=200)
        assert isinstance(json.loads(result)["data"], list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("create_contact", full_name="blocked", book_id=BOOK_ID)

    @pytest.mark.asyncio
    async def test_read_only_blocks_delete(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("delete_contact", uid="any", book_id=BOOK_ID)

    @pytest.mark.asyncio
    async def test_write_allows_create(self, nc_mcp_write: McpTestHelper) -> None:
        result = await nc_mcp_write.call("create_contact", full_name=f"{PREFIX}-write-ok", book_id=BOOK_ID)
        contact = json.loads(result)
        assert contact["uid"]
