import tempfile
import unittest
from pathlib import Path

import cli


def rss(link="https://blogs.nvidia.com/blog/robotics-test/", published="Fri, 06 Nov 2026 15:30:00 +0000", count=1):
    items = "".join(f'''<item><title>Robotics simulation announcement {index}</title>
        <link>{link if count == 1 else link + str(index) + '/'}</link>
        <description><![CDATA[<p>Vendor reports <b>robot simulation</b> capabilities.</p>]]></description>
        <pubDate>{published}</pubDate></item>''' for index in range(count))
    return f'<rss version="2.0"><channel>{items}</channel></rss>'.encode()


class CompanySourceTests(unittest.TestCase):
    def setUp(self):
        self.source = next(x for x in cli.sources() if x["id"] == "nvidia_robotics")

    def test_publication_and_evidence_are_not_research_claims(self):
        item = cli.parse(self.source, rss())[0]
        self.assertEqual(item["kind"], "company_announcement")
        self.assertEqual(item["published_at"], "2026-11-06T15:30:00+00:00")
        self.assertEqual(item["publication_date_basis"], "company_rss_pubdate")
        self.assertEqual(item["evidence_scope"], "official_company_rss_excerpt_only")
        self.assertEqual(item["claims_status"], "vendor_claims_not_independently_verified")
        self.assertIsNone(item["version_updated_at"])
        self.assertNotIn("<", item["summary"])

    def test_caps_recent_items_and_accepts_empty_feed(self):
        self.assertEqual(len(cli.parse(self.source, rss(count=12))), 10)
        self.assertEqual(cli.parse(self.source, rss(count=0)), [])

    def test_only_configured_fetch_endpoint_and_official_article_links(self):
        self.assertEqual(cli.valid_url(self.source["url"], fetch=True), self.source["url"])
        for url in ["https://blogs.nvidia.com/feed/", "https://blogs.nvidia.com/blog/robotics-test/"]:
            with self.assertRaises(ValueError):
                cli.valid_url(url, fetch=True)
        for url in ["https://github.com/nvidia/example", "https://blogs.nvidia.com.evil.test/blog/x/",
                    "https://blogs.nvidia.com/blog/x/?redirect=external", "https://blogs.nvidia.com/"]:
            with self.assertRaises(ValueError):
                cli.parse(self.source, rss(link=url))

    def test_rejects_wrong_feed_shape_and_missing_timezone(self):
        for raw in [b'<html>blocked</html>', rss(published=""), rss(published="Fri, 06 Nov 2026 15:30:00")]:
            with self.assertRaises(ValueError):
                cli.parse(self.source, raw)

    def test_repeat_ingest_preserves_date_and_editorial(self):
        with tempfile.TemporaryDirectory() as directory:
            with cli.connect(Path(directory) / "research.sqlite") as db:
                fetch = lambda source: (rss(), {"http_status": 200, "sha256": "fixture"})
                first = cli.ingest_source(db, self.source, fetch)
                item = cli.export_data(db)["items"][0]
                cli.edit(db, item["id"], {"summary": "人工核读：仅厂商公告"}, 0)
                again = cli.ingest_source(db, self.source, fetch)
                exported = cli.export_data(db)
                self.assertEqual((first["status"], again["status"]), ("updated", "no_change"))
                self.assertEqual(exported["counts"]["works"], 1)
                self.assertEqual(exported["items"][0]["published_at"], "2026-11-06T15:30:00+00:00")
                self.assertEqual(exported["items"][0]["editorial"]["summary"], "人工核读：仅厂商公告")
                self.assertIn("simulation", exported["items"][0]["domain_ids"])


if __name__ == "__main__":
    unittest.main()
