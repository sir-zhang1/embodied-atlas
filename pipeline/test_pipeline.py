import json
import tempfile
import unittest
from pathlib import Path
import cli


def atom(version="v1", title="Tactile perception for robot manipulation", doi="10.1234/ROBOT.1"):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
    <entry><id>http://arxiv.org/abs/2406.09246{version}</id><title>{title}</title>
    <summary>A multimodal tactile representation for manipulation. Author-reported experiments.</summary>
    <published>2024-06-13T15:46:55Z</published><updated>2024-09-05T19:46:34Z</updated>
    <arxiv:doi>{doi}</arxiv:doi><author><name>Example Author</name></author></entry></feed>'''.encode()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = cli.connect(Path(self.temp.name)/"research.sqlite")
        self.source = cli.sources()[0]
        self.domains = cli.framework()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def run_source(self, raw):
        return cli.ingest_source(self.db,self.source,lambda source:(raw,{"http_status":200,"sha256":"fixture"}),self.domains)

    def test_dedupe_versions_and_doi(self):
        first = self.run_source(atom())
        again = self.run_source(atom())
        newer = self.run_source(atom("v2"))
        self.assertEqual((first["status"],again["status"],newer["status"]),("updated","no_change","updated"))
        data = cli.export_data(self.db)
        self.assertEqual(data["counts"]["works"],1)
        self.assertEqual(data["counts"]["versions"],2)
        item = data["items"][0]
        self.assertEqual(item["doi"],"10.1234/robot.1")
        self.assertEqual(item["arxiv_id"],"2406.09246")
        self.assertIn("tactile_perception",item["domain_ids"])
        self.assertIn("perception",item["domain_ids"])

    def test_editorial_survives_ingestion_and_stale_write_rejected(self):
        self.run_source(atom())
        wid = cli.export_data(self.db)["items"][0]["id"]
        edited = cli.edit(self.db,wid,{"summary":"人工摘要：视觉与触觉的实验边界","commentary":"维护者观点"},0)
        self.assertEqual(edited["revision"],1)
        self.run_source(atom("v2"))
        item = cli.export_data(self.db)["items"][0]
        self.assertEqual(item["editorial"]["summary"],"人工摘要：视觉与触觉的实验边界")
        self.assertNotEqual(item["summary"],item["editorial"]["summary"])
        with self.assertRaises(cli.RevisionConflict):
            cli.edit(self.db,wid,{"summary":"stale overwrite"},0)
        self.assertEqual(len(cli.search(self.db,"触觉")),1)
        self.assertEqual(len(cli.search(self.db,"TACTILE")),1)
        self.assertEqual(cli.search(self.db,"' OR 1=1 --"),[])
        self.assertEqual(cli.search(self.db,"%"),[])

    def test_empty_is_success_and_failure_preserves_previous_success(self):
        self.run_source(atom())
        empty = self.run_source(b'<feed xmlns="http://www.w3.org/2005/Atom"/>')
        failed = self.run_source(b'<html>blocked</html>')
        self.assertEqual(empty["status"],"no_change")
        self.assertEqual(empty["items_seen"],0)
        self.assertEqual(failed["status"],"failed")
        source = cli.export_data(self.db)["sources"][0]
        self.assertEqual(source["last_status"],"failed")
        self.assertEqual(source["last_success_at"],empty["finished_at"])
        self.assertEqual(cli.export_data(self.db)["counts"]["works"],1)

    def test_public_export_has_no_private_rows(self):
        self.run_source(atom())
        self.db.execute("CREATE TABLE private_notes(owner TEXT,note TEXT)")
        self.db.execute("INSERT INTO private_notes VALUES('owner','PRIVATE_SENTINEL_123')")
        data = json.dumps(cli.export_data(self.db))
        self.assertNotIn("PRIVATE_SENTINEL_123",data)
        self.assertNotIn("private_notes",data)

    def test_rss_does_not_replace_api_date_or_create_false_changes(self):
        self.run_source(atom())
        source = next(s for s in cli.sources() if s["parser"] == "arxiv_rss")
        raw = b'<rss version="2.0"><channel><item><title>Short RSS title</title><link>https://arxiv.org/abs/2406.09246v1</link><description>RSS summary</description><pubDate>Sun, 08 Nov 2026 05:00:00 +0000</pubDate></item></channel></rss>'
        cli.ingest_source(self.db,source,lambda s:(raw,{"http_status":200}),self.domains)
        again = self.run_source(atom())
        item = cli.export_data(self.db)["items"][0]
        self.assertEqual(item["published_at"],"2024-06-13T15:46:55Z")
        self.assertEqual(again["status"],"no_change")
        self.assertEqual(len(item["source_ids"]),2)

    def test_network_and_xml_trust_boundaries(self):
        for url in ["https://127.0.0.1/", "https://huggingface.co@evil.test/", "http://api.github.com/repos/huggingface/lerobot/releases", "https://api.github.com/user"]:
            with self.assertRaises(ValueError):
                cli.valid_url(url,fetch=True)
        with self.assertRaises(ValueError):
            cli.parse(self.source,b'<!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]><feed/>')
        self.assertEqual(cli.arxiv_identifier("https://arxiv.org/pdf/2406.09246v3.pdf"),("2406.09246","v3"))
        self.assertEqual(cli.doi_identifier("https://doi.org/10.1234/ROBOT.1"),"10.1234/robot.1")

    def test_identifier_collision_is_reviewable_and_atomic(self):
        self.run_source(atom())
        item = cli.paper_record("Another work","x","2406.00001","v1",doi="10.1234/other")
        with self.db:
            cli.upsert(self.db,self.source,item,cli.now(),self.domains)
        conflict = self.run_source(atom(doi="10.1234/other"))
        self.assertEqual(conflict["status"],"failed")
        self.assertIn("Identifier conflict",conflict["error"])
        self.assertEqual(cli.export_data(self.db)["counts"]["works"],2)


if __name__ == "__main__":
    unittest.main()
