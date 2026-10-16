"""Recall V2 — time and lifecycle filters.

Time filters are **query behaviour, not new canonical meaning**.  They read the
canonical ``created_at`` / ``updated_at`` / ``graveyard_at`` / ``resurrected_at``
timestamps that v0.1 already stores, and never write anything.

Lifecycle filtering works the same way over v0.1's ``type`` and ``status``
columns.  One rule from the audit is enforced here and tested: a Graveyard hint
in the query *annotates* rather than silently excluding, because the thought a
user abandoned two years ago and resurrected last week is often exactly what
they are reaching for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from ..store import THOUGHT_STATUSES, THOUGHT_TYPES

MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "couple": 2, "few": 3,
}

_SPACING = re.compile(r"\s+")


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse v0.1's canonical UTC ISO-8601 timestamps, tolerating second
    precision (which imports from other tooling may carry)."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _utc(datetime.fromisoformat(text))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return _utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def to_timestamp(moment: datetime) -> str:
    """Canonical timestamp format used by v0.1 (microsecond UTC, ``Z`` suffix)."""
    return _utc(moment).astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime
    label: str
    expression: str = ""
    source: str = "explicit"     # 'explicit' (a filter) | 'query-hint' (detected)
    #: How much precision the window claims.  A filter the user set is EXPLICIT.
    #: A phrase like "last spring" read out of the query is an ESTIMATED_RANGE:
    #: it is a real range the user remembers, but IWTT must never present it as
    #: an exact timestamp it does not have.  Surfaced in the UI and in the
    #: diagnostics so nobody mistakes an estimate for a measurement.
    precision: str = "EXPLICIT"  # EXPLICIT | ESTIMATED_RANGE

    def contains(self, value: Any) -> bool:
        moment = parse_timestamp(value) if isinstance(value, str) else value
        if moment is None:
            return False
        moment = _utc(moment)
        return self.start <= moment <= self.end

    def date_bounds(self) -> tuple[str, str]:
        """``YYYY-MM-DD`` bounds for the cheap SQL prefilter.

        Comparing only the date prefix is deliberate: it is immune to timestamp
        precision differences between locally created records and imported ones,
        while the exact containment test above is still applied in Python to the
        bounded candidate set.
        """
        return (
            self.start.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            self.end.astimezone(timezone.utc).strftime("%Y-%m-%d"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": to_timestamp(self.start),
            "end": to_timestamp(self.end),
            "label": self.label,
            "expression": self.expression,
            "source": self.source,
            "precision": self.precision,
            "estimated": self.precision == "ESTIMATED_RANGE",
            "note": (
                "A range read from your wording, not a timestamp IWTT knows. "
                "Thoughts anywhere inside it can match."
                if self.precision == "ESTIMATED_RANGE"
                else "An explicit window you set as a filter."
            ),
        }


def _start_of_day(moment: datetime) -> datetime:
    local = _utc(moment).astimezone(timezone.utc)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _end_of_day(moment: datetime) -> datetime:
    return _start_of_day(moment) + timedelta(days=1) - timedelta(microseconds=1)


def _start_of_week(moment: datetime) -> datetime:
    day = _start_of_day(moment)
    return day - timedelta(days=day.weekday())     # Monday, ISO convention


def _start_of_month(moment: datetime) -> datetime:
    return _start_of_day(moment).replace(day=1)


def _start_of_year(moment: datetime) -> datetime:
    return _start_of_day(moment).replace(month=1, day=1)


def _add_months(moment: datetime, months: int) -> datetime:
    total = (moment.year * 12 + (moment.month - 1)) + months
    year, month = divmod(total, 12)
    day = min(moment.day, _days_in_month(year, month + 1))
    return moment.replace(year=year, month=month + 1, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (_add_months_safe(year, month + 1) - _add_months_safe(year, month)).days


def _add_months_safe(year: int, month: int) -> datetime:
    total = (year * 12 + (month - 1))
    resolved_year, resolved_month = divmod(total, 12)
    return datetime(resolved_year, resolved_month + 1, 1, tzinfo=timezone.utc)


NAMED_WINDOWS: dict[str, str] = {
    "today": "today",
    "yesterday": "yesterday",
    "week": "this week",
    "this week": "this week",
    "last week": "last week",
    "month": "this month",
    "this month": "this month",
    "last month": "last month",
    "year": "this year",
    "this year": "this year",
    "last year": "last year",
    "all": "",
    "any": "",
    "": "",
}


def window_for(label: str, *, now: datetime | None = None) -> TimeWindow | None:
    """Resolve a named window (``today``/``this week``/``last month``/…)."""
    moment = _utc(now or datetime.now(timezone.utc))
    key = (label or "").strip().lower()
    resolved = NAMED_WINDOWS.get(key, key)
    if not resolved:
        return None
    if resolved == "today":
        return TimeWindow(_start_of_day(moment), _end_of_day(moment), "today", key)
    if resolved == "yesterday":
        previous = moment - timedelta(days=1)
        return TimeWindow(_start_of_day(previous), _end_of_day(previous), "yesterday", key)
    if resolved == "this week":
        start = _start_of_week(moment)
        return TimeWindow(start, start + timedelta(days=7) - timedelta(microseconds=1),
                          "this week", key)
    if resolved == "last week":
        start = _start_of_week(moment) - timedelta(days=7)
        return TimeWindow(start, start + timedelta(days=7) - timedelta(microseconds=1),
                          "last week", key)
    if resolved == "this month":
        start = _start_of_month(moment)
        return TimeWindow(start, _add_months(start, 1) - timedelta(microseconds=1),
                          "this month", key)
    if resolved == "last month":
        start = _add_months(_start_of_month(moment), -1)
        return TimeWindow(start, _add_months(start, 1) - timedelta(microseconds=1),
                          "last month", key)
    if resolved == "this year":
        start = _start_of_year(moment)
        return TimeWindow(start, start.replace(month=12, day=31, hour=23, minute=59,
                                               second=59, microsecond=999999),
                          "this year", key)
    if resolved == "last year":
        start = _start_of_year(moment).replace(year=moment.year - 1)
        return TimeWindow(start, start.replace(month=12, day=31, hour=23, minute=59,
                                               second=59, microsecond=999999),
                          "last year", key)
    return None


_DATE = r"(?P<year>\d{4})(?:[-/](?P<month>\d{1,2}))?(?:[-/](?P<day>\d{1,2}))?"
_MONTH_YEAR = r"(?P<monthname>" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\s+(?P<myear>\d{4})"


def _parse_date_expression(text: str) -> tuple[datetime, datetime] | None:
    """Parse a date-ish fragment into an inclusive ``(start, end)`` pair."""
    text = text.strip().strip(".,;:")
    if not text:
        return None
    match = re.fullmatch(_MONTH_YEAR, text.lower())
    if match:
        month = MONTHS[match.group("monthname")]
        year = int(match.group("myear"))
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        return start, _add_months(start, 1) - timedelta(microseconds=1)
    match = re.fullmatch(_DATE, text)
    if match:
        year = int(match.group("year"))
        month = int(match.group("month") or 1)
        day = int(match.group("day") or 1)
        try:
            start = datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
        if match.group("day"):
            return start, _end_of_day(start)
        if match.group("month"):
            return start, _add_months(start, 1) - timedelta(microseconds=1)
        return start, start.replace(month=12, day=31, hour=23, minute=59, second=59,
                                    microsecond=999999)
    lowered = text.lower()
    if lowered in MONTHS:
        return None     # a bare month name with no year is not a date
    return None


def parse_time_expression(expression: str, *, now: datetime | None = None) -> TimeWindow | None:
    """Parse an explicit time filter expression.

    Supported: named windows; ``before X``; ``after X`` / ``since X``;
    ``between X and Y`` / ``from X to Y``; ``in YEAR``; a bare date;
    ``N day(s)|week(s)|month(s)|year(s) ago``; the rolling forms
    ``last|past N days/weeks/months/years``; and ``recently`` / ``lately``.

    A rolling window is used where the phrase is rolling ("last 30 days" ends
    now) and a calendar window where the phrase is calendrical ("last month" is
    the whole of the previous month).  IWTT never narrows a phrase beyond the
    precision the phrase itself carries.
    """
    moment = _utc(now or datetime.now(timezone.utc))
    text = _SPACING.sub(" ", (expression or "").strip().lower())
    if not text:
        return None

    named = window_for(text, now=moment)
    if named:
        return TimeWindow(named.start, named.end, named.label, expression, "explicit")

    match = re.fullmatch(r"between (.+?) and (.+)", text)
    if match:
        left = _parse_date_expression(match.group(1))
        right = _parse_date_expression(match.group(2))
        if left and right:
            return TimeWindow(left[0], right[1], f"between {match.group(1)} and {match.group(2)}",
                              expression)
    match = re.fullmatch(r"from (.+?) to (.+)", text)
    if match:
        left = _parse_date_expression(match.group(1))
        right = _parse_date_expression(match.group(2))
        if left and right:
            return TimeWindow(left[0], right[1], f"from {match.group(1)} to {match.group(2)}",
                              expression)
    match = re.fullmatch(r"(?:before|prior to|older than|until)\s+(.+)", text)
    if match:
        bound = _parse_date_expression(match.group(1))
        if bound:
            return TimeWindow(datetime(1970, 1, 1, tzinfo=timezone.utc), bound[1],
                              f"before {match.group(1)}", expression)
    match = re.fullmatch(r"(?:after|since|from|newer than)\s+(.+)", text)
    if match:
        bound = _parse_date_expression(match.group(1))
        if bound:
            return TimeWindow(bound[0], moment + timedelta(days=3650),
                              f"after {match.group(1)}", expression)
    match = re.fullmatch(r"in\s+(.+)", text)
    if match:
        bound = _parse_date_expression(match.group(1))
        if bound:
            return TimeWindow(bound[0], bound[1], f"in {match.group(1)}", expression)
    match = re.fullmatch(
        r"(?P<count>\d+|[a-z]+)\s*(?P<unit>day|week|month|year)s?\s+ago", text
    )
    if match:
        raw_count = match.group("count")
        count = int(raw_count) if raw_count.isdigit() else _WORD_NUMBERS.get(raw_count, 0)
        if count > 0:
            return _ago_window(count, match.group("unit"), moment, expression)
    match = re.fullmatch(
        r"(?:last|past|previous)\s+(?P<count>\d+|[a-z]+)\s*(?P<unit>day|week|month|year)s?", text
    )
    if match:
        raw_count = match.group("count")
        count = int(raw_count) if raw_count.isdigit() else _WORD_NUMBERS.get(raw_count, 0)
        if count > 0:
            return _rolling_window(count, match.group("unit"), moment, expression)
    if text in ("recently", "lately", "recent", "just recently"):
        return _rolling_window(30, "day", moment, expression)

    bound = _parse_date_expression(text)
    if bound:
        return TimeWindow(bound[0], bound[1], text, expression)
    return None


def _rolling_window(count: int, unit: str, moment: datetime, expression: str) -> TimeWindow:
    """``last N units`` — a window that ends now.

    Bounded at 3660 days so an absurd input ("last 999999 years") cannot produce
    a range that makes every timestamp match and silently disables the filter.
    """
    if unit == "day":
        delta = timedelta(days=count)
    elif unit == "week":
        delta = timedelta(weeks=count)
    elif unit == "month":
        delta = timedelta(days=30 * count)
    else:
        delta = timedelta(days=365 * count)
    if delta > timedelta(days=3660):
        delta = timedelta(days=3660)
    start = _start_of_day(moment - delta)
    label = f"last {count} {unit}{'s' if count > 1 else ''}"
    return TimeWindow(start, moment, label, expression)


def _ago_window(count: int, unit: str, moment: datetime, expression: str) -> TimeWindow:
    """``N units ago`` resolves to the calendar unit containing that instant.

    Deterministic and documented: "two years ago" means the whole of that year,
    "three months ago" the whole of that month, and so on.  IWTT does not
    fabricate a narrower precision than the phrase actually carries.
    """
    if unit == "day":
        target = moment - timedelta(days=count)
        return TimeWindow(_start_of_day(target), _end_of_day(target),
                          f"{count} {unit}{'s' if count > 1 else ''} ago", expression)
    if unit == "week":
        target = _start_of_week(moment) - timedelta(weeks=count)
        return TimeWindow(target, target + timedelta(days=7) - timedelta(microseconds=1),
                          f"{count} week{'s' if count > 1 else ''} ago", expression)
    if unit == "month":
        target = _add_months(_start_of_month(moment), -count)
        return TimeWindow(target, _add_months(target, 1) - timedelta(microseconds=1),
                          f"{count} month{'s' if count > 1 else ''} ago", expression)
    target_year = moment.year - count
    start = datetime(target_year, 1, 1, tzinfo=timezone.utc)
    return TimeWindow(start, start.replace(month=12, day=31, hour=23, minute=59, second=59,
                                           microsecond=999999),
                      f"{count} year{'s' if count > 1 else ''} ago", expression)


_AGO_PATTERN = re.compile(
    r"(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten|couple|few)\s+"
    r"(?P<unit>day|week|month|year)s?\s+ago"
)
_IN_YEAR_PATTERN = re.compile(r"\b(?:in|during|from|back in)\s+(?P<year>(?:19|20)\d{2})\b")
_BARE_YEAR_PATTERN = re.compile(r"\b(?P<year>(?:19|20)\d{2})\b")
_NAMED_HINT_PATTERN = re.compile(
    r"\b(?:today|yesterday|this week|last week|past week|this month|last month|"
    r"past month|this year|last year|past year|recently|lately)\b"
)


def detect_time_hint(query: str, *, now: datetime | None = None) -> TimeWindow | None:
    """Find a time hint *inside* a natural-language recall query.

    Priority is fixed and documented, so the same query always resolves the same
    way: explicit range → ``N units ago`` → ``in YEAR`` → named window →
    bare year.  A hint found here is a **soft** signal (a boost plus an
    annotation); only an explicit filter narrows results.  That distinction is
    the honest response to "do not fabricate precision when the query is vague".
    """
    moment = _utc(now or datetime.now(timezone.utc))
    text = _SPACING.sub(" ", (query or "").strip().lower())
    if not text:
        return None

    for pattern in (
        r"between (.+?) and (?=\d{4}|[a-z]+ )",
        r"from (.+?) to (?=\d{4}|[a-z]+ )",
    ):
        match = re.search(pattern, text)
        if match:
            resolved = parse_time_expression(match.group(0), now=moment)
            if resolved:
                return TimeWindow(resolved.start, resolved.end, resolved.label,
                                  resolved.expression, "query-hint", "ESTIMATED_RANGE")

    match = _AGO_PATTERN.search(text)
    if match:
        raw = match.group("count")
        count = int(raw) if raw.isdigit() else _WORD_NUMBERS.get(raw, 0)
        if count:
            window = _ago_window(count, match.group("unit"), moment, match.group(0))
            return TimeWindow(window.start, window.end, window.label, window.expression,
                              "query-hint", "ESTIMATED_RANGE")

    match = _IN_YEAR_PATTERN.search(text)
    if match:
        year = int(match.group("year"))
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        return TimeWindow(start, start.replace(month=12, day=31, hour=23, minute=59,
                                               second=59, microsecond=999999),
                          f"in {year}", match.group(0), "query-hint", "ESTIMATED_RANGE")

    match = _NAMED_HINT_PATTERN.search(text)
    if match:
        phrase = match.group(0)
        if phrase in ("recently", "lately"):
            start = _start_of_day(moment) - timedelta(days=30)
            return TimeWindow(start, moment, "recently (last 30 days)", phrase, "query-hint", "ESTIMATED_RANGE")
        normalized_phrase = phrase.replace("past ", "last ")
        resolved = window_for(normalized_phrase, now=moment)
        if resolved:
            return TimeWindow(resolved.start, resolved.end, resolved.label, phrase, "query-hint", "ESTIMATED_RANGE")

    match = _BARE_YEAR_PATTERN.search(text)
    if match:
        year = int(match.group("year"))
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        return TimeWindow(start, start.replace(month=12, day=31, hour=23, minute=59,
                                               second=59, microsecond=999999),
                          f"in {year}", match.group(0), "query-hint", "ESTIMATED_RANGE")
    return None


@dataclass
class RecallFilters:
    """Hard filters.  Everything here narrows the candidate set; nothing here
    reinterprets canonical meaning."""

    types: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()          # ACTIVE / GRAVEYARD; empty = both
    tags: tuple[str, ...] = ()
    tag_mode: str = "any"                   # 'any' | 'all'
    time: TimeWindow | None = None
    time_field: str = "created_at"          # created_at | updated_at | graveyard_at
    include_demo: bool = True
    limit: int = 25

    def normalized(self) -> "RecallFilters":
        types = tuple(sorted({t.upper() for t in self.types if t and t.upper() in THOUGHT_TYPES}))
        statuses = tuple(
            sorted({s.upper() for s in self.statuses if s and s.upper() in THOUGHT_STATUSES})
        )
        tags = tuple(dict.fromkeys(t.strip() for t in self.tags if t and t.strip()))
        mode = "all" if str(self.tag_mode).lower() == "all" else "any"
        field = self.time_field if self.time_field in (
            "created_at", "updated_at", "graveyard_at", "resurrected_at"
        ) else "created_at"
        return RecallFilters(
            types=types,
            statuses=statuses,
            tags=tags,
            tag_mode=mode,
            time=self.time,
            time_field=field,
            include_demo=bool(self.include_demo),
            limit=max(1, min(int(self.limit or 25), 500)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "types": list(self.types),
            "statuses": list(self.statuses),
            "tags": list(self.tags),
            "tagMode": self.tag_mode,
            "time": self.time.to_dict() if self.time else None,
            "timeField": self.time_field,
            "includeDemo": self.include_demo,
            "limit": self.limit,
        }

    @classmethod
    def from_params(cls, params: dict[str, Any], *, now: datetime | None = None) -> "RecallFilters":
        """Build filters from HTTP/API parameters."""
        def as_list(value: Any) -> list[str]:
            if value is None:
                return []
            if isinstance(value, (list, tuple, set)):
                return [str(item) for item in value if str(item).strip()]
            text = str(value).strip()
            if not text:
                return []
            return [part.strip() for part in re.split(r"[,|]", text) if part.strip()]

        time_window: TimeWindow | None = None
        for key in ("time", "window", "when"):
            if params.get(key):
                time_window = parse_time_expression(str(params[key]), now=now)
                if time_window:
                    break
        since = params.get("since") or params.get("after")
        until = params.get("until") or params.get("before")
        if since or until:
            start = parse_time_expression(f"after {since}", now=now) if since else None
            end = parse_time_expression(f"before {until}", now=now) if until else None
            moment = _utc(now or datetime.now(timezone.utc))
            time_window = TimeWindow(
                start.start if start else datetime(1970, 1, 1, tzinfo=timezone.utc),
                end.end if end else moment + timedelta(days=3650),
                f"{since or 'beginning'} → {until or 'now'}",
                f"since={since or ''} until={until or ''}",
                "explicit",
            )
        statuses = as_list(params.get("status") or params.get("statuses"))
        graveyard_only = str(params.get("graveyard", "")).lower() in {"1", "true", "yes", "only"}
        if graveyard_only and not statuses:
            statuses = ["GRAVEYARD"]
        return cls(
            types=tuple(as_list(params.get("type") or params.get("types"))),
            statuses=tuple(statuses),
            tags=tuple(as_list(params.get("tags") or params.get("tag"))),
            tag_mode=str(params.get("tagMode", "any")).lower(),
            time=time_window,
            time_field=str(params.get("timeField", "created_at")),
            include_demo=str(params.get("includeDemo", "true")).lower()
            not in {"0", "false", "no"},
            limit=int(params.get("limit", 25) or 25),
        )


def matches_filters(
    filters: RecallFilters,
    *,
    thought_type: str,
    status: str,
    created_at: str,
    updated_at: str | None = None,
    graveyard_at: str | None = None,
    resurrected_at: str | None = None,
    is_demo: bool = False,
    tag_names: Sequence[str] = (),
) -> bool:
    """Exact containment test, applied in Python to the bounded candidate set."""
    if filters.types and thought_type not in filters.types:
        return False
    if filters.statuses and status not in filters.statuses:
        return False
    if not filters.include_demo and is_demo:
        return False
    if filters.tags:
        normalized_tags = {_fold(tag) for tag in tag_names}
        wanted = {_fold(tag) for tag in filters.tags}
        if filters.tag_mode == "all":
            if not wanted <= normalized_tags:
                return False
        elif not (wanted & normalized_tags):
            return False
    if filters.time:
        value = {
            "created_at": created_at,
            "updated_at": updated_at or created_at,
            "graveyard_at": graveyard_at,
            "resurrected_at": resurrected_at,
        }.get(filters.time_field)
        if not value or not filters.time.contains(value):
            return False
    return True


#: Human wording for each rejection dimension, used so a filtered-out candidate
#: is *reported* rather than silently vanishing.
REJECTION_LABELS: dict[str, str] = {
    "type": "thought type",
    "status": "lifecycle status (the Graveyard is hidden by this filter)",
    "demo": "demo records are excluded by this filter",
    "tag": "tag",
    "time": "time window",
}


def rejection_reason(
    filters: RecallFilters,
    *,
    thought_type: str,
    status: str,
    created_at: str,
    updated_at: str | None = None,
    graveyard_at: str | None = None,
    resurrected_at: str | None = None,
    is_demo: bool = False,
    tag_names: Sequence[str] = (),
) -> str | None:
    """Why a candidate was filtered out, or ``None`` if it was kept.

    ``matches_filters()`` answers a yes/no question; this answers *which* rule
    said no, so the engine can tell the user "3 candidates were hidden by your
    Graveyard filter" instead of "nothing matched".  Silently excluding a
    candidate is how a retrieval system starts lying by omission.
    """
    if filters.types and thought_type not in filters.types:
        return "type"
    if filters.statuses and status not in filters.statuses:
        return "status"
    if not filters.include_demo and is_demo:
        return "demo"
    if filters.tags:
        normalized_tags = {_fold(tag) for tag in tag_names}
        wanted = {_fold(tag) for tag in filters.tags}
        if filters.tag_mode == "all":
            if not wanted <= normalized_tags:
                return "tag"
        elif not (wanted & normalized_tags):
            return "tag"
    if filters.time:
        value = {
            "created_at": created_at,
            "updated_at": updated_at or created_at,
            "graveyard_at": graveyard_at,
            "resurrected_at": resurrected_at,
        }.get(filters.time_field)
        if not value or not filters.time.contains(value):
            return "time"
    return None


def filter_report(
    filters: RecallFilters, rejected: Sequence[tuple[Any, str]]
) -> dict[str, Any]:
    """Summarise exclusions by dimension, with a few example titles.

    ``rejected`` is ``(candidate, reason)``.  Returns a JSON-ready record that the
    outcome carries under ``honesty["filteredOut"]``.
    """
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for candidate, reason in rejected:
        counts[reason] = counts.get(reason, 0) + 1
        bucket = examples.setdefault(reason, [])
        title = getattr(candidate, "display_title", "") or getattr(candidate, "thought_id", "")
        if len(bucket) < 3 and title:
            bucket.append(title[:80])
    return {
        "totalExcluded": len(rejected),
        "byReason": {key: counts[key] for key in sorted(counts)},
        "labels": {key: REJECTION_LABELS.get(key, key) for key in sorted(counts)},
        "examples": {key: examples[key] for key in sorted(examples)},
        "filtersActive": {
            "types": list(filters.types),
            "statuses": list(filters.statuses),
            "tags": list(filters.tags),
            "tagMode": filters.tag_mode,
            "time": filters.time.to_dict() if filters.time else None,
            "timeField": filters.time_field,
            "includeDemo": filters.include_demo,
            "limit": filters.limit,
        },
        "note": (
            "These candidates matched your words but were excluded by a filter you set. "
            "Nothing was lost — remove the filter and they come back."
        ),
    }


def _fold(tag: str) -> str:
    from ..store import normalize_text

    return normalize_text(tag)


def sql_prefilter(filters: RecallFilters) -> tuple[str, list[Any]]:
    """Coarse SQL prefilter over ``recall_docs``.

    Deliberately conservative: it may admit rows that ``matches_filters`` later
    rejects (the date bound compares only ``YYYY-MM-DD``), but it must never
    reject a row that would pass.  Tag filtering is not done here because it
    would require a join for every query; tags are applied to the bounded
    candidate set instead.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if filters.types:
        clauses.append(f"d.type IN ({','.join('?' for _ in filters.types)})")
        params.extend(filters.types)
    if filters.statuses:
        clauses.append(f"d.status IN ({','.join('?' for _ in filters.statuses)})")
        params.extend(filters.statuses)
    if not filters.include_demo:
        clauses.append("d.is_demo = 0")
    if filters.time:
        column = filters.time_field if filters.time_field in (
            "created_at", "updated_at"
        ) else "created_at"
        start_date, end_date = filters.time.date_bounds()
        clauses.append(f"substr(d.{column},1,10) BETWEEN ? AND ?")
        params.extend([start_date, end_date])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params
