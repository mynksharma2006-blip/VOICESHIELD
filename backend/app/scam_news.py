from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import logging
import re
from urllib.parse import urlsplit
from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree


LOGGER = logging.getLogger(__name__)
MAX_FEED_BYTES = 1_000_000
MAX_ITEM_AGE = timedelta(days=60)
MAX_ITEMS = 20
USER_AGENT = "VoiceShieldScamSafety/1.0"


COUNTRIES = {
    "AU": {
        "name": "Australia",
        "source_name": "Scamwatch (ACCC)",
        "source_url": "https://www.scamwatch.gov.au/",
    },
    "CA": {
        "name": "Canada",
        "source_name": "Canadian Anti-Fraud Centre",
        "source_url": "https://antifraudcentre-centreantifraude.ca/index-eng.htm",
    },
    "GB": {
        "name": "United Kingdom",
        "source_name": "GOV.UK",
        "source_url": "https://www.gov.uk/",
        "feed_url": "https://www.gov.uk/search/all.atom?keywords=scam",
        "feed_host": "www.gov.uk",
    },
    "IN": {
        "name": "India",
        "source_name": "Press Information Bureau of India",
        "source_url": "https://pib.gov.in/",
        "feed_url": "https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3&reg=48",
        "feed_host": "www.pib.gov.in",
    },
    "US": {
        "name": "United States",
        "source_name": "Federal Trade Commission",
        "source_url": "https://consumer.ftc.gov/scams",
    },
}

SCAM_TERMS = (
    "scam",
    "fraud",
    "phishing",
    "impersonat",
    "identity theft",
    "cyber crime",
    "online safety",
    "suspicious email",
    "suspicious text",
    "digital arrest",
    "fake call",
    "fake website",
)
ATOM_NS = "{http://www.w3.org/2005/Atom}"


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class _MetaDescription(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.description = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        if values.get("property", "").casefold() == "og:description":
            self.description = values.get("content", "")


def _plain_text(markup: str) -> str:
    parser = _PlainText()
    parser.feed(markup)
    return " ".join(" ".join(parser.parts).split())


def _parse_date(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_article_url(value: str, allowed_host: str) -> str | None:
    parsed = urlsplit(value)
    allowed_hosts = {allowed_host}
    if allowed_host == "www.pib.gov.in":
        allowed_hosts.add("pib.gov.in")
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in allowed_hosts:
        return None
    return value


def _read_official_url(url: str, allowed_host: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml, text/html",
        },
    )
    with urlopen(request, timeout=8) as response:
        allowed_hosts = {allowed_host}
        if allowed_host == "www.pib.gov.in":
            allowed_hosts.add("pib.gov.in")
        if (urlsplit(response.geturl()).hostname or "").lower() not in allowed_hosts:
            raise ValueError("The news source redirected outside its official domain.")
        content = response.read(MAX_FEED_BYTES + 1)
    if len(content) > MAX_FEED_BYTES:
        raise ValueError("The official news response exceeded the size limit.")
    return content


def _read_gov_uk_feed(now: datetime) -> tuple[list[dict[str, str]], str | None]:
    config = COUNTRIES["GB"]
    content = _read_official_url(config["feed_url"], config["feed_host"])
    root = ElementTree.fromstring(content)
    feed_updated = _parse_date(root.findtext(f"{ATOM_NS}updated", default=""))
    dated_items: list[tuple[datetime, dict[str, str]]] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        headline = _plain_text(entry.findtext(f"{ATOM_NS}title", default=""))
        summary = _plain_text(entry.findtext(f"{ATOM_NS}summary", default=""))
        updated = _parse_date(entry.findtext(f"{ATOM_NS}updated", default=""))
        link = next(
            (
                item.attrib.get("href", "")
                for item in entry.findall(f"{ATOM_NS}link")
                if item.attrib.get("rel", "alternate") == "alternate"
            ),
            "",
        )
        if (
            not headline
            or not summary
            or updated is None
            or now - MAX_ITEM_AGE > updated
            or updated > now + timedelta(days=1)
            or not any(term in f"{headline} {summary}".casefold() for term in SCAM_TERMS)
        ):
            continue
        safe_link = _safe_article_url(link, config["feed_host"])
        if safe_link is None:
            continue
        dated_items.append(
            (
                updated,
                {
                    "headline": headline,
                    "date": updated.isoformat(),
                    "source": "GOV.UK (UK Government)",
                    "summary": (
                        summary[:297].rstrip() + "..."
                        if len(summary) > 300
                        else summary
                    ),
                    "url": safe_link,
                },
            )
        )
    dated_items.sort(key=lambda item: item[0], reverse=True)
    items = [item for _, item in dated_items[:MAX_ITEMS]]
    return items, feed_updated.isoformat() if feed_updated else None


def _read_pib_feed(now: datetime) -> tuple[list[dict[str, str]], str | None]:
    config = COUNTRIES["IN"]
    root = ElementTree.fromstring(_read_official_url(config["feed_url"], config["feed_host"]))
    channel = root.find("channel")
    if channel is None:
        raise ValueError("The official India news feed has no channel.")

    feed_updated = _parse_date(channel.findtext("lastBuildDate", default=""))
    dated_items: list[tuple[datetime, dict[str, str]]] = []
    for entry in channel.findall("item"):
        headline = _plain_text(entry.findtext("title", default=""))
        link = entry.findtext("link", default="").strip()
        if not headline or not any(term in headline.casefold() for term in SCAM_TERMS):
            continue
        safe_link = _safe_article_url(link, config["feed_host"])
        if safe_link is None:
            continue

        article = _read_official_url(safe_link, config["feed_host"]).decode("utf-8", "replace")
        date_match = re.search(
            r"\b(\d{1,2}\s+[A-Z]{3}\s+\d{4}\s+\d{1,2}:\d{2}\s*(?:AM|PM))\b",
            article,
            re.IGNORECASE,
        )
        description = _MetaDescription()
        description.feed(article)
        summary = _plain_text(description.description)
        if date_match is None or not summary:
            continue
        try:
            local_date = datetime.strptime(date_match.group(1), "%d %b %Y %I:%M%p")
        except ValueError:
            continue
        updated = local_date.replace(tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(
            timezone.utc
        )
        if now - MAX_ITEM_AGE > updated or updated > now + timedelta(days=1):
            continue
        dated_items.append(
            (
                updated,
                {
                    "headline": headline,
                    "date": updated.isoformat(),
                    "source": "Press Information Bureau (Government of India)",
                    "summary": (
                        summary[:297].rstrip() + "..."
                        if len(summary) > 300
                        else summary
                    ),
                    "url": safe_link,
                },
            )
        )
        if len(dated_items) >= MAX_ITEMS:
            break

    dated_items.sort(key=lambda item: item[0], reverse=True)
    items = [item for _, item in dated_items[:MAX_ITEMS]]
    return items, feed_updated.isoformat() if feed_updated else None


def get_scam_news(country_code: str) -> dict[str, object]:
    checked_at = datetime.now(timezone.utc)
    country = COUNTRIES.get(country_code)
    if country is None:
        raise ValueError("Choose a country from the available list.")

    base: dict[str, object] = {
        "country_code": country_code,
        "country_name": country["name"],
        "source_name": country["source_name"],
        "source_url": country["source_url"],
        "checked_at": checked_at.isoformat(),
        "feed_updated_at": None,
        "items": [],
    }
    if "feed_url" not in country:
        base.update(
            {
                "status": "unavailable",
                "message": (
                    f"A live feed from {country['source_name']} is not configured yet. "
                    "No sample or cached alerts are being shown; visit the official source instead."
                ),
            }
        )
        return base

    try:
        if country_code == "GB":
            items, feed_updated = _read_gov_uk_feed(checked_at)
        elif country_code == "IN":
            items, feed_updated = _read_pib_feed(checked_at)
        else:
            raise ValueError(f"No feed parser is configured for {country_code}.")
    except (OSError, URLError, TimeoutError, ValueError, ElementTree.ParseError) as exc:
        LOGGER.warning("Could not load the %s scam news feed: %s", country_code, exc)
        base.update(
            {
                "status": "unavailable",
                "message": (
                    f"The live feed from {country['source_name']} could not be reached or read. "
                    "No sample or cached alerts are being shown. Try again later or visit the official source."
                ),
            }
        )
        return base

    base["feed_updated_at"] = feed_updated
    base["items"] = items
    if not items:
        base.update(
            {
                "status": "empty",
                "message": (
                    "The feed loaded, but it contains no recent scam-related items with usable "
                    "dates and summaries. Visit the official source for the latest information."
                ),
            }
        )
    else:
        base.update(
            {
                "status": "available",
                "message": f"Recent items from {country['source_name']}. Open each original report for full details.",
            }
        )
    return base
