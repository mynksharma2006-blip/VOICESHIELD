from datetime import datetime, timezone
from io import BytesIO
import unittest
from unittest.mock import patch

from app.scam_news import _read_gov_uk_feed, _read_pib_feed, get_scam_news


class _FakeResponse(BytesIO):
    def __init__(self, content: bytes, url: str = "https://www.gov.uk/search/all.atom?keywords=scam"):
        super().__init__(content)
        self.url = url

    def geturl(self) -> str:
        return self.url


class ScamNewsTests(unittest.TestCase):
    def test_feed_returns_recent_relevant_items_only(self) -> None:
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <updated>2026-10-24T12:00:00Z</updated>
          <entry>
            <title>Older scam warning</title>
            <updated>2026-01-01T12:00:00Z</updated>
            <link rel="alternate" href="https://www.gov.uk/older"/>
            <summary>Scam warning summary</summary>
          </entry>
          <entry>
            <title>Recent scam alert</title>
            <updated>2026-10-23T12:00:00Z</updated>
            <link rel="alternate" href="https://www.gov.uk/recent"/>
            <summary>Beware of a &lt;mark&gt;scam&lt;/mark&gt; request.</summary>
          </entry>
          <entry>
            <title>Recent unrelated news</title>
            <updated>2026-10-22T12:00:00Z</updated>
            <link rel="alternate" href="https://www.gov.uk/unrelated"/>
            <summary>A recent public update about agriculture.</summary>
          </entry>
          <entry>
            <title>Recent unsafe link</title>
            <updated>2026-10-21T12:00:00Z</updated>
            <link rel="alternate" href="https://example.com/scam"/>
            <summary>A scam report from an unexpected host.</summary>
          </entry>
        </feed>"""
        now = datetime(2026, 10, 24, tzinfo=timezone.utc)
        with patch("app.scam_news.urlopen", return_value=_FakeResponse(xml)):
            items, feed_updated = _read_gov_uk_feed(now)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["headline"], "Recent scam alert")
        self.assertEqual(items[0]["summary"], "Beware of a scam request.")
        self.assertEqual(items[0]["url"], "https://www.gov.uk/recent")
        self.assertEqual(feed_updated, "2026-10-24T12:00:00+00:00")

    def test_unconfigured_country_displays_no_sample_items(self) -> None:
        result = get_scam_news("US")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["items"], [])
        self.assertIn("not configured", result["message"])

    def test_pib_item_uses_official_article_date_and_description(self) -> None:
        feed = b"""<rss version="2.0"><channel><lastBuildDate>Sat, 24 Oct 2026 12:00:00 +0000</lastBuildDate>
          <item><title>Government warns about online fraud</title>
          <link>https://pib.gov.in/PressReleaseIframePage.aspx?PRID=42</link></item>
        </channel></rss>"""
        article = """<html><head><meta property="og:description" content="A short official warning about online fraud." /></head>
          <body><div>24 OCT 2026 5:30PM by PIB Delhi</div></body></html>""".encode()
        responses = [
            _FakeResponse(feed, "https://www.pib.gov.in/RssMain.aspx"),
            _FakeResponse(article, "https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=42"),
        ]
        now = datetime(2026, 10, 24, 13, 0, tzinfo=timezone.utc)
        with patch("app.scam_news.urlopen", side_effect=responses):
            items, feed_updated = _read_pib_feed(now)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["headline"], "Government warns about online fraud")
        self.assertEqual(items[0]["summary"], "A short official warning about online fraud.")
        self.assertEqual(items[0]["date"], "2026-10-24T12:00:00+00:00")
        self.assertEqual(feed_updated, "2026-10-24T12:00:00+00:00")

    def test_unknown_country_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            get_scam_news("ZZ")


if __name__ == "__main__":
    unittest.main()
