"""Client for AF Bostäder laundry booking (Aptus booking service).

Flow:
1. Log in to afbostader.se through the ASP.NET WebForms login form (cookie session).
2. Load "Dina sidor -> Boka tvättid", which embeds an Aptus token in #hidAptusToken.
3. Use that token against the JSON booking service (bookingservice.svc).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from html.parser import HTMLParser
import logging
import re
from typing import Any

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.afbostader.se"
LOGIN_URL = f"{BASE_URL}/"
BOOKING_PAGE_URL = f"{BASE_URL}/dina/sidor/boka-tvattid/"
APTUS_URL = "https://afbostader.se/bookingservice/bookingservice.svc/"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TIMEOUT = aiohttp.ClientTimeout(total=30)


class AfbError(Exception):
    """Generic AF Bostäder error."""


class AfbAuthError(AfbError):
    """Login failed / credentials invalid."""


class AfbTokenError(AfbError):
    """Aptus token rejected (expired session)."""


@dataclass
class LaundrySlot:
    """A laundry pass, either booked by the user or available."""

    start: datetime
    end: datetime
    group_id: str | None
    group_name: str | None
    location_name: str | None
    booking_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "group": self.group_name,
            "location": self.location_name,
            **({"booking_id": self.booking_id} if self.booking_id else {}),
        }


@dataclass
class LaundryData:
    """Snapshot of the user's laundry situation."""

    bookings: list[LaundrySlot] = field(default_factory=list)
    available: list[LaundrySlot] = field(default_factory=list)


class _FormParser(HTMLParser):
    """Collects <input> elements and hidden Aptus values from a page."""

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "input":
            self.inputs.append({k.lower(): (v or "") for k, v in attrs})


def _parse_inputs(html: str) -> list[dict[str, str]]:
    parser = _FormParser()
    parser.feed(html)
    return parser.inputs


def _input_value(inputs: list[dict[str, str]], element_id: str) -> str | None:
    for inp in inputs:
        if inp.get("id") == element_id:
            return inp.get("value", "")
    return None


def build_login_payload(html: str, username: str, password: str) -> dict[str, str]:
    """Build the WebForms postback for the header login control."""
    inputs = _parse_inputs(html)
    payload: dict[str, str] = {}
    for inp in inputs:
        name = inp.get("name")
        if name and inp.get("type", "").lower() == "hidden" and name.startswith("__"):
            payload[name] = inp.get("value", "")

    user_field = next(
        (
            i["name"]
            for i in inputs
            if i.get("name", "").endswith("LoginControl$UserName")
        ),
        None,
    )
    if user_field is None:
        raise AfbError("Could not find login form on afbostader.se")
    prefix = user_field.removesuffix("UserName")
    button = next(
        (
            i
            for i in inputs
            if i.get("name", "").startswith(prefix)
            and i.get("type", "").lower() == "submit"
        ),
        None,
    )

    payload["__EVENTTARGET"] = ""
    payload["__EVENTARGUMENT"] = ""
    payload[f"{prefix}UserName"] = username
    payload[f"{prefix}Password"] = password
    payload[f"{prefix}chkRememberMe"] = "on"
    if button is not None:
        payload[button["name"]] = button.get("value", "Logga in")
    return payload


def _parse_hhmm(value: str) -> tuple[int, int]:
    """Parse '7.00', '07:00', '07.00.00' etc. into (hour, minute)."""
    match = re.match(r"\s*(\d{1,2})[.:](\d{2})", value)
    if not match:
        raise ValueError(f"Unparseable time {value!r}")
    return int(match.group(1)), int(match.group(2))


def _parse_timestamp(value: str, tz: tzinfo) -> datetime:
    """Parse Aptus timestamps like '2026-10-28T07:00' or '2026-10-28 07.00'."""
    date_part, _, time_part = value.replace("T", " ").partition(" ")
    day = date.fromisoformat(date_part.strip())
    hour, minute = _parse_hhmm(time_part) if time_part else (0, 0)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def parse_bookings(res: dict[str, Any], tz: tzinfo) -> list[LaundrySlot]:
    """Parse a GetCustomerBookings response."""
    slots: list[LaundrySlot] = []
    for item in res.get("CustomerBookings") or []:
        try:
            start = _parse_timestamp(item["StartTimeStamp"], tz)
            length = int(item.get("LengthMinutes") or 0)
            if length:
                end = start + timedelta(minutes=length)
            else:
                end = _parse_timestamp(item["StopTime"], tz)
        except (KeyError, ValueError) as err:
            _LOGGER.debug("Skipping unparseable booking %s: %s", item, err)
            continue
        slots.append(
            LaundrySlot(
                start=start,
                end=end,
                group_id=str(item.get("GroupId")) if item.get("GroupId") else None,
                group_name=item.get("GroupName"),
                location_name=item.get("LocationName"),
                booking_id=str(item.get("BookingId")) if item.get("BookingId") else None,
            )
        )
    return sorted(slots, key=lambda s: s.start)


def parse_groups(
    res: dict[str, Any], category_id: int | None
) -> tuple[dict[str, tuple[str, str]], date | None]:
    """Parse GetCustomerGroups into {group_id: (group_name, location_name)}.

    Returns the groups and the last bookable date.
    """
    categories = res.get("Categories") or []
    selected = [c for c in categories if category_id is not None and c.get("IconId") == category_id]
    if not selected:
        selected = categories

    groups: dict[str, tuple[str, str]] = {}
    last_bookable: date | None = None
    for cat in selected:
        for loc in cat.get("Locations") or []:
            for grp in loc.get("Groups") or []:
                groups[str(grp["Id"])] = (grp.get("Name", ""), loc.get("Name", ""))
                ts = grp.get("LastBookableTimestamp")
                if ts:
                    # The website treats the day before this timestamp as the last bookable day.
                    day = date.fromisoformat(ts.split("T")[0]) - timedelta(days=1)
                    if last_bookable is None or day > last_bookable:
                        last_bookable = day
    return groups, last_bookable


def parse_calendar(
    res: dict[str, Any],
    groups: dict[str, tuple[str, str]],
    tz: tzinfo,
) -> list[LaundrySlot]:
    """Parse GetCalendarData into a list of bookable passes."""
    intervals: dict[tuple[Any, Any], tuple[str, int]] = {}
    for pattern in res.get("IntervalPatterns") or []:
        for interval in pattern.get("Intervals") or []:
            intervals[(pattern.get("Id"), interval.get("PassNo"))] = (
                interval.get("StartTime", ""),
                int(interval.get("LengthMinutes") or 0),
            )

    slots: list[LaundrySlot] = []
    for day in res.get("Days") or []:
        try:
            day_date = date.fromisoformat(str(day["Date"]).split("T")[0])
        except (KeyError, ValueError):
            continue
        for day_group in day.get("DayGroups") or []:
            group_id = day_group.get("GroupId")
            group_name, location_name = groups.get(str(group_id), (None, None))
            for bookable in day_group.get("BookablePasses") or []:
                interval = intervals.get((day_group.get("IntervalPatternId"), bookable.get("No")))
                if interval is None:
                    continue
                try:
                    hour, minute = _parse_hhmm(interval[0])
                except ValueError:
                    continue
                start = datetime(
                    day_date.year, day_date.month, day_date.day, hour, minute, tzinfo=tz
                )
                slots.append(
                    LaundrySlot(
                        start=start,
                        end=start + timedelta(minutes=interval[1]),
                        group_id=str(group_id) if group_id is not None else None,
                        group_name=group_name,
                        location_name=location_name,
                    )
                )
    return sorted(slots, key=lambda s: (s.start, s.location_name or "", s.group_name or ""))


class AfbClient:
    """Async client for AF Bostäder laundry booking."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        tz: tzinfo,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._tz = tz
        self._token: str | None = None
        self._category_id: int | None = None
        # Cookies are kept here and sent verbatim. aiohttp's cookie jar re-quotes values
        # containing e.g. ',' or ' ' (Python http.cookies style "\054"), which the
        # Azure WAF in front of afbostader.se rejects with 403.
        self._cookies: dict[str, str] = {}

    def _store_cookies(self, resp: aiohttp.ClientResponse) -> None:
        for raw in resp.headers.getall("Set-Cookie", []):
            name, sep, rest = raw.partition("=")
            if not sep:
                continue
            value = rest.split(";", 1)[0]
            attrs = rest.lower()
            if not value or "expires=thu, 01-jan-1970" in attrs or "max-age=0" in attrs:
                self._cookies.pop(name.strip(), None)
            else:
                self._cookies[name.strip()] = value

    async def _site_request(
        self, method: str, url: str, data: dict[str, str] | None = None
    ) -> tuple[str, str]:
        """Request a page on www.afbostader.se, following redirects manually.

        Returns (final_url, html).
        """
        for _ in range(10):
            headers = {"User-Agent": USER_AGENT}
            if self._cookies:
                headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            if method == "POST":
                headers["Referer"] = LOGIN_URL
                headers["Origin"] = BASE_URL
            async with self._session.request(
                method,
                url,
                data=data,
                headers=headers,
                allow_redirects=False,
                timeout=TIMEOUT,
            ) as resp:
                self._store_cookies(resp)
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "/")
                    url = str(resp.url.join(URL(location)))
                    if resp.status in (301, 302, 303):
                        method, data = "GET", None
                    continue
                if resp.status == 403:
                    raise AfbError(f"Blocked by afbostader.se firewall (403) at {resp.url}")
                resp.raise_for_status()
                return str(resp.url), await resp.text()
        raise AfbError("Too many redirects")

    async def login(self) -> None:
        """Log in and fetch a fresh Aptus token."""
        self._cookies.clear()
        _, html = await self._site_request("GET", LOGIN_URL)
        payload = build_login_payload(html, self._username, self._password)
        await self._site_request("POST", LOGIN_URL, payload)
        final_url, html = await self._site_request("GET", BOOKING_PAGE_URL)

        if "returnurl=" in final_url.lower():
            raise AfbAuthError("Invalid email or password")

        inputs = _parse_inputs(html)
        token = _input_value(inputs, "hidAptusToken")
        if not token:
            raise AfbError("Logged in, but no laundry booking token was found on the page")
        self._token = token
        category = _input_value(inputs, "hidAptusCategoryId")
        self._category_id = int(category) if category and category.isdigit() else None
        _LOGGER.debug("Logged in to AF Bostäder (category %s)", self._category_id)

    async def _aptus_get(self, method: str, params: dict[str, str]) -> dict[str, Any]:
        if self._token is None:
            await self.login()
        query = {"Token": self._token or "", **params}
        async with self._session.get(
            APTUS_URL + method,
            params=query,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "sv-SE"},
            timeout=TIMEOUT,
        ) as resp:
            if resp.status == 401:
                self._token = None
                raise AfbTokenError("Aptus token rejected")
            if resp.status >= 400:
                text = await resp.text()
                raise AfbError(f"{method} failed ({resp.status}): {text[:200]}")
            return await resp.json(content_type=None)

    async def _aptus_get_retry(self, method: str, params: dict[str, str]) -> dict[str, Any]:
        """Call the Aptus service, re-logging in once if the token expired."""
        try:
            return await self._aptus_get(method, params)
        except AfbTokenError:
            await self.login()
            return await self._aptus_get(method, params)

    async def async_get_data(self, days_ahead_limit: int | None = None) -> LaundryData:
        """Fetch the user's bookings and the available passes."""
        now = datetime.now(self._tz)

        bookings_res = await self._aptus_get_retry("GetCustomerBookings", {})
        bookings = [b for b in parse_bookings(bookings_res, self._tz) if b.end > now]

        groups_res = await self._aptus_get_retry("GetCustomerGroups", {})
        groups, last_bookable = parse_groups(groups_res, self._category_id)

        available: list[LaundrySlot] = []
        if groups:
            date_from = now.date()
            date_to = last_bookable or date_from + timedelta(days=14)
            if days_ahead_limit is not None:
                date_to = min(date_to, date_from + timedelta(days=days_ahead_limit))
            date_to = max(date_to, date_from)
            calendar_res = await self._aptus_get_retry(
                "GetCalendarData",
                {
                    "DateFrom": date_from.isoformat(),
                    "DateTo": date_to.isoformat(),
                    "Groups": ",".join(groups),
                },
            )
            available = [s for s in parse_calendar(calendar_res, groups, self._tz) if s.start > now]

        return LaundryData(bookings=bookings, available=available)
