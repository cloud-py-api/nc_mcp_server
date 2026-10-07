"""Unit tests for calendar helpers: time zones, RRULE checks, video links, creator guard."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar as ICal

from nc_mcp_server.tools.calendar import (
    CREATED_BY_PROP,
    _apply_event_updates,
    _build_ical,
    _check_calendar_id,
    _check_summary,
    _format_event,
    _parse_dt,
    _parse_rrule,
    _principal,
    _share_body,
    _slug,
    _to_caldav_utc,
    _validate_url,
    _vevent,
    _zone,
    _zone_of,
)

BERLIN = ZoneInfo("Europe/Berlin")


class TestParseDt:
    def test_naive_without_zone_is_utc(self) -> None:
        assert _parse_dt("2026-10-20T19:00:00") == datetime(2026, 10, 20, 19, 0, tzinfo=UTC)

    def test_naive_is_wall_time_in_zone(self) -> None:
        dt = _parse_dt("2026-10-20T19:00:00", tz=BERLIN)
        assert isinstance(dt, datetime)
        assert dt.tzinfo == BERLIN
        assert dt.astimezone(UTC) == datetime(2026, 10, 20, 17, 0, tzinfo=UTC)

    def test_offset_keeps_instant_and_takes_zone(self) -> None:
        dt = _parse_dt("2026-12-01T18:00:00Z", tz=BERLIN)
        assert isinstance(dt, datetime)
        assert dt.tzinfo == BERLIN
        assert dt.hour == 19

    def test_all_day(self) -> None:
        assert _parse_dt("2026-10-20", tz=BERLIN) == date(2026, 10, 20)

    def test_unknown_zone(self) -> None:
        with pytest.raises(ValueError, match="Unknown timezone"):
            _zone("Mars/Olympus")


class TestCaldavUtc:
    def test_offset(self) -> None:
        assert _to_caldav_utc("2026-10-20T00:00:00+02:00", None) == "20261019T220000Z"

    def test_naive_in_zone(self) -> None:
        assert _to_caldav_utc("2026-01-10T00:00:00", BERLIN) == "20260109T230000Z"

    def test_date_in_zone(self) -> None:
        assert _to_caldav_utc("2026-07-01", BERLIN) == "20260630T220000Z"

    def test_utc_z(self) -> None:
        assert _to_caldav_utc("2026-04-01T00:00:00Z", BERLIN) == "20260401T000000Z"

    def test_garbage(self) -> None:
        with pytest.raises(ValueError, match="Invalid date/time"):
            _to_caldav_utc("next tuesday", None)


class TestRrule:
    def test_weekly(self) -> None:
        assert _parse_rrule("FREQ=WEEKLY;COUNT=4;BYDAY=MO,WE") == {
            "FREQ": ["WEEKLY"],
            "COUNT": [4],
            "BYDAY": ["MO", "WE"],
        }

    def test_prefix_and_case(self) -> None:
        assert _parse_rrule("RRULE:freq=monthly;byday=1tu") == {"FREQ": ["MONTHLY"], "BYDAY": ["1TU"]}

    @pytest.mark.parametrize(
        "rule",
        [
            "COUNT=3",
            "FREQ=HOURLY",
            "FREQ=SECONDLY",
            "FREQ=WEEKLY;COUNT=0",
            "FREQ=WEEKLY;COUNT=100000",
            "FREQ=WEEKLY;COUNT=2;UNTIL=20261231",
            "FREQ=WEEKLY;BYDAY=XX",
            "FREQ=MONTHLY;BYMONTHDAY=32",
            "FREQ=WEEKLY;X-FOO=1",
            "FREQ=WEEKLY;FREQ=DAILY",
            "FREQ=WEEKLY;BYHOUR=3",
            "FREQ",
        ],
    )
    def test_rejects(self, rule: str) -> None:
        with pytest.raises(ValueError, match="RRULE"):
            _parse_rrule(rule)

    def test_until_date_for_zoned_start_is_end_of_day_utc(self) -> None:
        start = datetime(2026, 10, 20, 19, 0, tzinfo=BERLIN)
        until = _parse_rrule("FREQ=WEEKLY;UNTIL=20261215", start)["UNTIL"][0]
        assert until == datetime(2026, 12, 15, 22, 59, 59, tzinfo=UTC)

    def test_until_naive_datetime_in_start_zone(self) -> None:
        start = datetime(2026, 7, 1, 19, 0, tzinfo=BERLIN)
        until = _parse_rrule("FREQ=DAILY;UNTIL=20260705T190000", start)["UNTIL"][0]
        assert until == datetime(2026, 7, 5, 17, 0, tzinfo=UTC)

    def test_until_for_all_day_is_date(self) -> None:
        until = _parse_rrule("FREQ=DAILY;UNTIL=20260705T235959Z", date(2026, 7, 1))["UNTIL"][0]
        assert until == date(2026, 7, 5)

    def test_until_utc_kept(self) -> None:
        until = _parse_rrule("FREQ=DAILY;UNTIL=20270705T235959Z")["UNTIL"][0]
        assert until == datetime(2027, 7, 5, 23, 59, 59, tzinfo=UTC)


class TestBuildIcal:
    def test_zoned_event_has_tzid_vtimezone_and_creator(self) -> None:
        start = datetime(2026, 10, 20, 19, 0, tzinfo=BERLIN)
        end = datetime(2026, 10, 20, 21, 0, tzinfo=BERLIN)
        text = _build_ical("u1", "Meetup", start, end, rrule="FREQ=MONTHLY;BYDAY=3TU", created_by="anna")
        assert "DTSTART;TZID=Europe/Berlin:20261020T190000" in text
        assert "BEGIN:VTIMEZONE" in text
        assert f"{CREATED_BY_PROP}:anna" in text
        event = _format_event(text, "anna")
        assert event["dtstart"] == "2026-10-20T19:00:00+02:00"
        assert event["created_by_me"] is True
        assert event["has_attendees"] is False
        assert _format_event(text, "bert")["created_by_me"] is False

    def test_conference_goes_to_location_when_empty(self) -> None:
        start = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)
        url = "https://cloud.example.org/index.php/call/abc123"
        text = _build_ical("u2", "Call", start, start, conference_url=url)
        event = _format_event(text)
        assert event["conference"] == url
        assert event["location"] == url
        assert "FEATURE=AUDIO,VIDEO" in text.replace("\r\n ", "")

    def test_conference_goes_to_description_with_location(self) -> None:
        start = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)
        url = "https://cloud.example.org/index.php/call/abc123"
        text = _build_ical("u3", "Meeting", start, start, description="Agenda", location="Room 1", conference_url=url)
        event = _format_event(text)
        assert event["location"] == "Room 1"
        assert event["description"] == f"Agenda\n\nVideo call: {url}"

    def test_attendees_never_listed(self) -> None:
        text = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VEVENT\r\nUID:a\r\n"
            "DTSTART:20261020T170000Z\r\nDTEND:20261020T180000Z\r\nSUMMARY:x\r\n"
            "ORGANIZER:mailto:o@example.org\r\nATTENDEE:mailto:p@example.org\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        event = _format_event(text, "o")
        assert event["has_attendees"] is True
        assert "example.org" not in str(event)


class TestUpdates:
    def _event(self, **kw: object) -> ICal:
        start = datetime(2026, 10, 20, 19, 0, tzinfo=BERLIN)
        return ICal.from_ical(_build_ical("u", "Meetup", start, start.replace(hour=21), **kw))  # type: ignore[arg-type]

    def test_new_start_keeps_event_zone(self) -> None:
        component = _vevent(self._event())
        zone = _zone_of(component.get("DTSTART"))
        assert zone == BERLIN
        _apply_event_updates(component, None, "2026-11-03T18:30:00", None, None, None, None, tz=zone)
        assert component.get("DTSTART").dt == datetime(2026, 11, 3, 18, 30, tzinfo=BERLIN)

    def test_moving_start_keeps_duration(self) -> None:
        component = _vevent(self._event())
        _apply_event_updates(component, None, "2026-11-03T18:00:00", None, None, None, None, tz=BERLIN)
        assert component.get("DTEND").dt == datetime(2026, 11, 3, 20, 0, tzinfo=BERLIN)

    def test_replace_and_remove_conference(self) -> None:
        component = _vevent(self._event(conference_url="https://x.example/call/old"))
        _apply_event_updates(component, None, None, None, None, None, None, conference_url="https://x.example/call/new")
        assert str(component.get("LOCATION")) == "https://x.example/call/new"
        _apply_event_updates(component, None, None, None, None, None, None, conference_url="")
        assert component.get("LOCATION") is None
        assert component.get("CONFERENCE") is None

    def test_remove_conference_line_from_description(self) -> None:
        component = _vevent(self._event(location="Room 1", description="Agenda", conference_url="https://x.example/c"))
        _apply_event_updates(component, None, None, None, None, None, None, conference_url="")
        assert str(component.get("DESCRIPTION")) == "Agenda"
        assert str(component.get("LOCATION")) == "Room 1"

    def test_rrule_set_and_cleared(self) -> None:
        component = _vevent(self._event())
        _apply_event_updates(component, None, None, None, None, None, None, rrule="FREQ=WEEKLY;COUNT=3")
        assert component.get("RRULE") is not None
        _apply_event_updates(component, None, None, None, None, None, None, rrule="")
        assert component.get("RRULE") is None

    def test_check_summary(self) -> None:
        component = _vevent(self._event())
        _check_summary(component, " meetup ", "u")
        _check_summary(component, "", "u")
        with pytest.raises(ValueError, match="Nothing was changed"):
            _check_summary(component, "Board meeting", "u")


class TestUrl:
    def test_ok(self) -> None:
        assert _validate_url(" https://a.example/call/x ") == "https://a.example/call/x"

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://a", "https://a b", ""])
    def test_rejects(self, url: str) -> None:
        with pytest.raises(ValueError, match="conference_url"):
            _validate_url(url)


class TestCalendarAdminHelpers:
    def test_slug(self) -> None:
        assert _slug("Sommerfest Ü 2027") == "sommerfest-ue-2027"
        assert _slug("!!!") == "calendar"

    def test_principal_and_share_body(self) -> None:
        assert _principal(" Board ", "group") == "principals/groups/Board"
        assert _principal("anna", "USER") == "principals/users/anna"
        with pytest.raises(ValueError, match="share_type"):
            _principal("anna", "email")
        with pytest.raises(ValueError, match="share_with"):
            _principal("a/b", "user")
        assert "<o:read-write/>" in _share_body("principals/users/a", True)
        assert "<o:read-write/>" not in _share_body("principals/users/a", False)
        assert "<o:remove>" in _share_body("principals/users/a", None)

    def test_calendar_id(self) -> None:
        assert _check_calendar_id("team_shared_by_alice") == "team_shared_by_alice"
        for bad in ("../x", "a/b", "inbox", "", "x" * 201):
            with pytest.raises(ValueError, match="calendar_id"):
                _check_calendar_id(bad)
