"""Contacts tools — list address books, query/create/update/delete contacts via CardDAV."""

import json
import uuid
import xml.etree.ElementTree as ET
from typing import Any

from icalendar import Calendar as ICal
from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..client import DAV_NS
from ..permissions import PermissionLevel, require_permission
from ..state import get_client, get_config

CARDDAV_NS = "urn:ietf:params:xml:ns:carddav"
CS_NS = "http://calendarserver.org/ns/"

ADDRESSBOOK_PROPFIND = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav"'
    ' xmlns:cs="http://calendarserver.org/ns/">'
    "<d:prop>"
    "<d:displayname/>"
    "<d:resourcetype/>"
    "<cs:getctag/>"
    "<card:addressbook-description/>"
    "</d:prop>"
    "</d:propfind>"
)

CONTACTS_REPORT = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
    "<d:prop>"
    "<d:getetag/>"
    "<card:address-data/>"
    "</d:prop>"
    "</card:addressbook-query>"
)

SKIP_BOOKS = {"z-server-generated--system", "z-app-generated--contactsinteraction--recent"}


def _vcard_escape(text: str) -> str:
    """Escape a string for use as a vCard 3.0 text value (RFC 2426 Section 2.4.2)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _normalize_entries(entries: list[dict[str, str]], default_type: str) -> list[dict[str, str]]:
    """Normalize typed entries, ensuring each has 'value' and a default 'type'."""
    result: list[dict[str, str]] = []
    for entry in entries:
        if "value" not in entry:
            raise ValueError(f'Invalid entry: {entry}. Must contain "value" key.')
        result.append({"value": str(entry["value"]), "type": str(entry.get("type", default_type))})
    return result


def _resolve_entries(
    single: str | None,
    multi: list[dict[str, str]] | None,
    default_type: str,
    param_single: str,
    param_multi: str,
) -> list[dict[str, str]] | None:
    """Resolve single-value and multi-value params into a typed entry list.

    Returns None if neither param was provided, empty list to clear, or populated list.
    """
    if single is not None and multi is not None:
        raise ValueError(f"Provide either '{param_single}' or '{param_multi}', not both.")
    if multi is not None:
        return _normalize_entries(multi, default_type)
    if single is not None:
        return [{"value": single, "type": default_type}] if single else []
    return None


def _carddav_path(user: str, book_id: str = "", resource: str = "") -> str:
    path = f"addressbooks/users/{user}/"
    if book_id:
        path += f"{book_id}/"
    if resource:
        path += resource
    return path


def _href_to_book_id(href: str) -> str:
    parts = href.rstrip("/").split("/")
    return parts[-1] if parts else ""


def _parse_addressbooks_xml(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)  # noqa: S314
    books: list[dict[str, Any]] = []
    for resp in root.findall(f"{{{DAV_NS}}}response"):
        href_el = resp.find(f"{{{DAV_NS}}}href")
        if href_el is None or not href_el.text:
            continue
        book_id = _href_to_book_id(href_el.text)
        if not book_id or book_id in SKIP_BOOKS:
            continue
        propstat = resp.find(f"{{{DAV_NS}}}propstat")
        if propstat is None:
            continue
        prop = propstat.find(f"{{{DAV_NS}}}prop")
        if prop is None:
            continue
        rt = prop.find(f"{{{DAV_NS}}}resourcetype")
        if rt is None or rt.find(f"{{{CARDDAV_NS}}}addressbook") is None:
            continue
        name_el = prop.find(f"{{{DAV_NS}}}displayname")
        ctag_el = prop.find(f"{{{CS_NS}}}getctag")
        desc_el = prop.find(f"{{{CARDDAV_NS}}}addressbook-description")
        books.append(
            {
                "id": book_id,
                "name": name_el.text if name_el is not None and name_el.text else book_id,
                "description": desc_el.text if desc_el is not None and desc_el.text else None,
                "ctag": ctag_el.text if ctag_el is not None and ctag_el.text else None,
            }
        )
    return books


def _parse_report_xml(xml_text: str) -> list[tuple[str, str, str]]:
    """Parse a CardDAV REPORT response into (href, etag, vcard_data) tuples."""
    root = ET.fromstring(xml_text)  # noqa: S314
    results: list[tuple[str, str, str]] = []
    for resp in root.findall(f"{{{DAV_NS}}}response"):
        href_el = resp.find(f"{{{DAV_NS}}}href")
        if href_el is None or not href_el.text:
            continue
        propstat = resp.find(f"{{{DAV_NS}}}propstat")
        if propstat is None:
            continue
        prop = propstat.find(f"{{{DAV_NS}}}prop")
        if prop is None:
            continue
        etag_el = prop.find(f"{{{DAV_NS}}}getetag")
        data_el = prop.find(f"{{{CARDDAV_NS}}}address-data")
        etag = etag_el.text.strip('"') if etag_el is not None and etag_el.text else ""
        vcard_data = data_el.text if data_el is not None and data_el.text else ""
        if vcard_data:
            results.append((href_el.text, etag, vcard_data))
    return results


def _typed_values(card: Any, prop_name: str) -> list[dict[str, str]]:
    """Extract multi-value properties like EMAIL, TEL, ADR as typed dicts."""
    items: list[dict[str, str]] = []
    for name, val in card.property_items():
        bare_name = name.split(".", 1)[1] if "." in name else name
        if bare_name != prop_name:
            continue
        params = dict(val.params) if hasattr(val, "params") else {}
        type_val = params.get("TYPE", "")
        if isinstance(type_val, list):
            type_val = ",".join(str(t) for t in type_val)
        type_str = str(type_val).upper() if type_val else ""
        if prop_name == "ADR" and hasattr(val, "fields"):
            f = val.fields
            addr_parts = [f.street, f.locality, f.region, f.postal_code, f.country]
            addr_str = ", ".join(p for p in addr_parts if p)
            if addr_str:
                entry: dict[str, str] = {"value": addr_str}
                if type_str:
                    entry["type"] = type_str
                items.append(entry)
        else:
            entry = {"value": str(val)}
            if type_str:
                entry["type"] = type_str
            items.append(entry)
    return items


def _parse_categories(card: Any) -> list[str]:
    """Extract categories from a vCard."""
    categories = card.get("CATEGORIES")
    if categories is None:
        return []
    cats = categories if isinstance(categories, list) else [categories]
    result: list[str] = []
    for cat in cats:
        if hasattr(cat, "__iter__") and not isinstance(cat, str):
            result.extend(str(c) for c in cat)
        else:
            result.append(str(cat))
    return result


def _parse_structured_name(card: Any) -> dict[str, str | None] | None:
    """Extract structured name (N field) from a vCard."""
    n = card.get("N")
    if n is None or not hasattr(n, "fields"):
        return None
    f = n.fields
    return {
        "family": str(f.family) if f.family else None,
        "given": str(f.given) if f.given else None,
        "additional": str(f.additional) if f.additional else None,
        "prefix": str(f.prefix) if f.prefix else None,
        "suffix": str(f.suffix) if f.suffix else None,
    }


def _format_contact(vcard_data: str) -> dict[str, Any]:
    """Parse vCard data into a clean contact dict, skipping PHOTO."""
    card = ICal.from_ical(vcard_data)
    contact: dict[str, Any] = {
        "uid": str(card.get("UID", "")),
        "full_name": str(card.get("FN", "")),
    }
    name = _parse_structured_name(card)
    if name:
        contact["name"] = name
    org = card.get("ORG")
    if org is not None:
        parts = org.ical_value if hasattr(org, "ical_value") else (str(org),)
        contact["organization"] = parts[0] if len(parts) == 1 else ";".join(parts)
    for field, key in [("TITLE", "title"), ("NOTE", "note"), ("BDAY", "birthday"), ("REV", "revision")]:
        val = card.get(field)
        if val is not None:
            contact[key] = str(val)
    for prop, key in [("EMAIL", "emails"), ("TEL", "phones"), ("ADR", "addresses")]:
        values = _typed_values(card, prop)
        if values:
            contact[key] = values
    cats = _parse_categories(card)
    if cats:
        contact["categories"] = cats
    return contact


def _build_vcard(fields: dict[str, Any]) -> str:
    """Build a vCard 3.0 string from a dict of fields."""
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    uid = fields.get("uid", f"mcp-{uuid.uuid4()}")
    lines.append(f"UID:{uid}")
    if fields.get("full_name"):
        lines.append(f"FN:{_vcard_escape(fields['full_name'])}")
    family = fields.get("family_name", "")
    given = fields.get("given_name", "")
    if family or given:
        lines.append(f"N:{_vcard_escape(family)};{_vcard_escape(given)};;;")
        if not fields.get("full_name"):
            fn = f"{given} {family}".strip()
            lines.append(f"FN:{_vcard_escape(fn)}")
    elif fields.get("full_name") and ";" not in fields.get("full_name", ""):
        parts = fields["full_name"].split(maxsplit=1)
        given = parts[0] if parts else ""
        family = parts[1] if len(parts) > 1 else ""
        lines.append(f"N:{_vcard_escape(family)};{_vcard_escape(given)};;;")
    for entry in fields.get("email_entries", []):
        type_part = f";TYPE={entry['type']}" if entry.get("type") else ""
        lines.append(f"EMAIL{type_part}:{_vcard_escape(entry['value'])}")
    for entry in fields.get("phone_entries", []):
        type_part = f";TYPE={entry['type']}" if entry.get("type") else ""
        lines.append(f"TEL{type_part}:{_vcard_escape(entry['value'])}")
    if fields.get("organization"):
        lines.append(f"ORG:{_vcard_escape(fields['organization'])}")
    if fields.get("title"):
        lines.append(f"TITLE:{_vcard_escape(fields['title'])}")
    if fields.get("note"):
        lines.append(f"NOTE:{_vcard_escape(fields['note'])}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def _find_contact(results: list[tuple[str, str, str]], uid: str) -> tuple[str, str, str] | None:
    """Find a contact by UID in REPORT results."""
    for href, etag, vcard_data in results:
        card = ICal.from_ical(vcard_data)
        if str(card.get("UID", "")) == uid:
            return href, etag, vcard_data
    return None


_UPDATE_FIELD_MAP = [
    ("full_name", "FN"),
    ("email_entries", "EMAIL"),
    ("phone_entries", "TEL"),
    ("organization", "ORG"),
    ("title", "TITLE"),
    ("note", "NOTE"),
    ("given_name", "N"),
    ("family_name", "N"),
]

_SIMPLE_UPDATE_FIELDS = [
    ("organization", "ORG"),
    ("title", "TITLE"),
    ("note", "NOTE"),
]


def _unfold_vcard_lines(vcard_data: str) -> list[str]:
    """Unfold vCard continuation lines (RFC 2426 §2.6 / RFC 6350 §3.2).

    Lines starting with a space or tab are continuations of the previous logical line.
    """
    lines: list[str] = []
    for raw_line in vcard_data.splitlines():
        clean = raw_line.rstrip("\r")
        if clean[:1] in (" ", "\t") and lines:
            lines[-1] += clean[1:]
        else:
            lines.append(clean)
    return lines


def _strip_updated_fields(lines: list[str], skip_fields: set[str]) -> list[str]:
    """Remove lines whose vCard field name is in skip_fields.

    Handles group prefixes (e.g. 'item1.EMAIL;TYPE=WORK:...' → field 'EMAIL').
    """
    result: list[str] = []
    for line in lines:
        field_name = line.split(";")[0].split(":")[0].upper() if ":" in line else ""
        if "." in field_name:
            field_name = field_name.split(".", 1)[1]
        if field_name not in skip_fields:
            result.append(line)
    return result


def _synthesize_fn(card: Any) -> str:
    """Derive a display name from a parsed vCard's existing N or FN fields."""
    old_n = card.get("N")
    if old_n and hasattr(old_n, "fields"):
        fn = f"{old_n.fields.given} {old_n.fields.family}".strip()
        if fn:
            return fn
    return str(card.get("FN", ""))


def _apply_name_updates(new_lines: list[str], insert_before: int, card: Any, updates: dict[str, Any]) -> bool:
    """Insert updated N and FN lines. Returns True if FN was inserted."""
    fn_inserted = False
    if updates.get("full_name"):
        new_lines.insert(insert_before, f"FN:{_vcard_escape(updates['full_name'])}")
        fn_inserted = True
    if "given_name" not in updates and "family_name" not in updates:
        return fn_inserted
    old_n = card.get("N")
    has_fields = old_n and hasattr(old_n, "fields")
    family = updates.get("family_name", str(old_n.fields.family) if has_fields else "")
    given = updates.get("given_name", str(old_n.fields.given) if has_fields else "")
    additional = str(old_n.fields.additional) if has_fields and old_n.fields.additional else ""
    prefix = str(old_n.fields.prefix) if has_fields and old_n.fields.prefix else ""
    suffix = str(old_n.fields.suffix) if has_fields and old_n.fields.suffix else ""
    n_parts = ";".join(_vcard_escape(p) for p in [family, given, additional, prefix, suffix])
    new_lines.insert(insert_before, f"N:{n_parts}")
    if not fn_inserted:
        derived_fn = f"{given} {family}".strip()
        if not derived_fn:
            derived_fn = _synthesize_fn(card)
        new_lines.insert(insert_before, f"FN:{_vcard_escape(derived_fn)}")
        fn_inserted = True
    return fn_inserted


def _apply_contact_updates(vcard_data: str, updates: dict[str, Any]) -> str:
    """Apply partial field updates to existing vCard data, return new vCard string."""
    skip_fields = {field for key, field in _UPDATE_FIELD_MAP if key in updates}
    if ("given_name" in updates or "family_name" in updates) and "full_name" not in updates:
        skip_fields.add("FN")
    new_lines = _strip_updated_fields(_unfold_vcard_lines(vcard_data), skip_fields)
    insert_before = len(new_lines) - 1
    card = ICal.from_ical(vcard_data)
    fn_inserted = _apply_name_updates(new_lines, insert_before, card, updates)
    if "FN" in skip_fields and not fn_inserted:
        new_lines.insert(insert_before, f"FN:{_vcard_escape(_synthesize_fn(card))}")
    for key, vcard_field in _SIMPLE_UPDATE_FIELDS:
        if updates.get(key):
            new_lines.insert(insert_before, f"{vcard_field}:{_vcard_escape(updates[key])}")
    for prop_name, entries_key in [("EMAIL", "email_entries"), ("TEL", "phone_entries")]:
        if entries_key in updates:
            for entry in updates[entries_key]:
                type_part = f";TYPE={entry['type']}" if entry.get("type") else ""
                new_lines.insert(insert_before, f"{prop_name}{type_part}:{_vcard_escape(entry['value'])}")
    return "\r\n".join(new_lines) + "\r\n"


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_addressbooks() -> str:
        """List address books for the current user.

        Returns user-owned address books (excludes system-generated books
        like Accounts and Recently Contacted).

        Returns:
            JSON list of address books with id, name, description, ctag.
        """
        config = get_config()
        client = get_client()
        response = await client.dav_request(
            "PROPFIND",
            _carddav_path(config.user),
            body=ADDRESSBOOK_PROPFIND,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context="List address books",
        )
        books = _parse_addressbooks_xml(response.text or "")
        return json.dumps(books, default=str)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_contacts(book_id: str = "contacts", limit: int = 50, offset: int = 0) -> str:
        """Get contacts from an address book.

        Args:
            book_id: Address book ID (default "contacts"). Use list_addressbooks to find IDs.
            limit: Maximum number of contacts to return (1-500, default 50).
            offset: Number of contacts to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of contact objects with uid, full_name, name,
            emails, phones, addresses, organization, title, note) and
            "pagination" (count, offset, limit, has_more).
        """
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        config = get_config()
        client = get_client()
        response = await client.dav_request(
            "REPORT",
            _carddav_path(config.user, book_id),
            body=CONTACTS_REPORT,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context=f"Get contacts from '{book_id}'",
        )
        results = _parse_report_xml(response.text or "")
        all_contacts = []
        for _href, etag, vcard_data in results:
            contact = _format_contact(vcard_data)
            contact["etag"] = etag
            all_contacts.append(contact)
        page = all_contacts[offset : offset + limit]
        has_more = offset + limit < len(all_contacts)
        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_contact(uid: str, book_id: str = "contacts") -> str:
        """Get a single contact by UID.

        Args:
            uid: The contact UID. Use get_contacts to find UIDs.
            book_id: Address book ID (default "contacts").

        Returns:
            JSON contact object with uid, full_name, name, emails, phones,
            addresses, organization, title, note, etag.
        """
        config = get_config()
        client = get_client()
        response = await client.dav_request(
            "REPORT",
            _carddav_path(config.user, book_id),
            body=CONTACTS_REPORT,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context=f"Get contact '{uid}' from '{book_id}'",
        )
        results = _parse_report_xml(response.text or "")
        found = _find_contact(results, uid)
        if found is None:
            raise ValueError(f"Contact with UID '{uid}' not found in address book '{book_id}'.")
        _href, etag, vcard_data = found
        contact = _format_contact(vcard_data)
        contact["etag"] = etag
        return json.dumps(contact, default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_contact(
        full_name: str = "",
        given_name: str = "",
        family_name: str = "",
        email: str = "",
        phone: str = "",
        emails: list[dict[str, str]] | None = None,
        phones: list[dict[str, str]] | None = None,
        organization: str = "",
        title: str = "",
        note: str = "",
        book_id: str = "contacts",
    ) -> str:
        """Create a new contact in an address book.

        Provide at least full_name, or given_name/family_name.

        For a single email/phone, use the email/phone params.
        For multiple, use emails/phones with an array of {"value","type"} objects:
          emails=[{"value":"a@b.com","type":"WORK"},{"value":"a@home.com","type":"HOME"}]
        Do not provide both email and emails (or phone and phones).

        Args:
            full_name: Full display name (e.g. "John Doe").
            given_name: First name (e.g. "John").
            family_name: Last name (e.g. "Doe").
            email: Single email address (convenience, adds as TYPE=WORK).
            phone: Single phone number (convenience, adds as TYPE=CELL).
            emails: Array of {"value","type"} for multiple emails. Overrides email.
            phones: Array of {"value","type"} for multiple phones. Overrides phone.
            organization: Company/organization name.
            title: Job title.
            note: Free-text note.
            book_id: Address book ID (default "contacts").

        Returns:
            JSON contact object with uid, full_name, and all set fields.
        """
        if not full_name and not given_name and not family_name:
            raise ValueError("At least one of full_name, given_name, or family_name is required.")
        email_entries = _resolve_entries(email or None, emails, "WORK", "email", "emails")
        phone_entries = _resolve_entries(phone or None, phones, "CELL", "phone", "phones")
        uid = f"mcp-{uuid.uuid4()}"
        vcard = _build_vcard(
            {
                "uid": uid,
                "full_name": full_name,
                "given_name": given_name,
                "family_name": family_name,
                "email_entries": email_entries or [],
                "phone_entries": phone_entries or [],
                "organization": organization,
                "title": title,
                "note": note,
            }
        )
        config = get_config()
        client = get_client()
        await client.dav_request(
            "PUT",
            _carddav_path(config.user, book_id, f"{uid}.vcf"),
            body=vcard,
            headers={"Content-Type": "text/vcard; charset=utf-8"},
            context=f"Create contact '{full_name or given_name}'",
        )
        response = await client.dav_request(
            "REPORT",
            _carddav_path(config.user, book_id),
            body=CONTACTS_REPORT,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context=f"Fetch created contact '{uid}'",
        )
        results = _parse_report_xml(response.text or "")
        found = _find_contact(results, uid)
        if found is None:
            return json.dumps({"uid": uid, "full_name": full_name or f"{given_name} {family_name}".strip()})
        _href, etag, vcard_data = found
        contact = _format_contact(vcard_data)
        contact["etag"] = etag
        return json.dumps(contact, default=str)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_contact(
        uid: str,
        etag: str,
        full_name: str | None = None,
        given_name: str | None = None,
        family_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        emails: list[dict[str, str]] | None = None,
        phones: list[dict[str, str]] | None = None,
        organization: str | None = None,
        title: str | None = None,
        note: str | None = None,
        book_id: str = "contacts",
    ) -> str:
        """Update an existing contact. Only provided fields are changed.

        Pass an empty string to clear a scalar field (e.g. note="").
        For emails/phones, pass [] to remove all.
        Do not provide both email and emails (or phone and phones).

        Requires the contact's current etag (from get_contacts or get_contact)
        to prevent conflicting updates.

        Args:
            uid: The contact UID to update.
            etag: Current ETag for conflict detection. Get from get_contacts/get_contact.
            full_name: New full display name.
            given_name: New first name.
            family_name: New last name.
            email: Set a single email (replaces all, TYPE=WORK). Pass "" to remove all.
            phone: Set a single phone (replaces all, TYPE=CELL). Pass "" to remove all.
            emails: Array of {"value","type"} to replace all emails. Pass [] to clear.
            phones: Array of {"value","type"} to replace all phones. Pass [] to clear.
            organization: New organization. Pass "" to remove.
            title: New job title. Pass "" to remove.
            note: New note. Pass "" to remove.
            book_id: Address book ID (default "contacts").

        Returns:
            JSON with the updated contact object.
        """
        updates: dict[str, Any] = {
            key: val
            for key, val in [
                ("full_name", full_name),
                ("given_name", given_name),
                ("family_name", family_name),
                ("organization", organization),
                ("title", title),
                ("note", note),
            ]
            if val is not None
        }
        email_entries = _resolve_entries(email, emails, "WORK", "email", "emails")
        if email_entries is not None:
            updates["email_entries"] = email_entries
        phone_entries = _resolve_entries(phone, phones, "CELL", "phone", "phones")
        if phone_entries is not None:
            updates["phone_entries"] = phone_entries
        if not updates:
            raise ValueError("At least one field must be provided for update.")
        config = get_config()
        client = get_client()
        response = await client.dav_request(
            "REPORT",
            _carddav_path(config.user, book_id),
            body=CONTACTS_REPORT,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context=f"Fetch contact '{uid}' for update",
        )
        results = _parse_report_xml(response.text or "")
        found = _find_contact(results, uid)
        if found is None:
            raise ValueError(f"Contact with UID '{uid}' not found in address book '{book_id}'.")
        href, _old_etag, vcard_data = found
        new_vcard = _apply_contact_updates(vcard_data, updates)
        resource = href.split(f"/{book_id}/", 1)[1] if f"/{book_id}/" in href else f"{uid}.vcf"
        await client.dav_request(
            "PUT",
            _carddav_path(config.user, book_id, resource),
            body=new_vcard,
            headers={"Content-Type": "text/vcard; charset=utf-8", "If-Match": f'"{etag}"'},
            context=f"Update contact '{uid}'",
        )
        response2 = await client.dav_request(
            "REPORT",
            _carddav_path(config.user, book_id),
            body=CONTACTS_REPORT,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context=f"Fetch updated contact '{uid}'",
        )
        results2 = _parse_report_xml(response2.text or "")
        found2 = _find_contact(results2, uid)
        if found2 is None:
            return json.dumps({"uid": uid, "status": "updated"})
        _href2, etag2, vcard_data2 = found2
        contact = _format_contact(vcard_data2)
        contact["etag"] = etag2
        return json.dumps(contact, default=str)


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_contact(uid: str, book_id: str = "contacts") -> str:
        """Permanently delete a contact from an address book.

        Args:
            uid: The contact UID to delete.
            book_id: Address book ID (default "contacts").

        Returns:
            Confirmation message.
        """
        config = get_config()
        client = get_client()
        response = await client.dav_request(
            "REPORT",
            _carddav_path(config.user, book_id),
            body=CONTACTS_REPORT,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context=f"Find contact '{uid}' for deletion",
        )
        results = _parse_report_xml(response.text or "")
        found = _find_contact(results, uid)
        if found is None:
            raise ValueError(f"Contact with UID '{uid}' not found in address book '{book_id}'.")
        href, _etag, _vcard_data = found
        resource = href.split(f"/{book_id}/", 1)[1] if f"/{book_id}/" in href else f"{uid}.vcf"
        await client.dav_request(
            "DELETE",
            _carddav_path(config.user, book_id, resource),
            context=f"Delete contact '{uid}'",
        )
        return f"Contact '{uid}' deleted from address book '{book_id}'."


def register(mcp: FastMCP) -> None:
    """Register Contacts tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
