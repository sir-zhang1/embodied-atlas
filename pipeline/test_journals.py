import copy
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.parse

import cli


SOURCE={"id":"journal_test","name":"Robotics Test","url":"https://api.crossref.org/journals/2470-9476/works",
        "parser":"crossref","priority":50,"journal_id":"science_robotics","issn":"2470-9476","scope":"robotics","initial_lookback_days":180}


def record(doi="10.1234/robot"):
    return {"DOI":doi,"type":"journal-article","ISSN":["2470-9476"],"title":["Tactile learning for robot manipulation"],
            "abstract":"<jats:p>"+"robot tactile learning "*20+"</jats:p>","container-title":["Science Robotics"],
            "published":{"date-parts":[[2026]]},"published-online":{"date-parts":[[2026,10,20]]}}


def response(items,cursor=None):
    raw=json.dumps({"status":"ok","message-type":"work-list","message":{"items":items,"next-cursor":cursor}}).encode()
    return raw,{"http_status":200,"sha256":cli.hashlib.sha256(raw).hexdigest()}


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=cli.connect(Path(self.temp.name)/"test.sqlite")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_publisher_metadata_dates_excerpt_and_identity(self):
        item=cli.parse(SOURCE,response([record()])[0])[0]
        self.assertEqual((item['doi'],item['kind'],item['version']),("10.1234/robot","paper","published"))
        self.assertEqual(item['published_at'],"2026-10-20")
        self.assertLessEqual(len(item['summary'].split()),24)
        self.assertTrue(item['abstract_available'])
        self.assertIn('tactile_perception',item['topic_matches'])
        self.assertNotIn('impact_factor',item)

    def test_partial_dates_are_not_fabricated(self):
        for parts,expected in [([2026],"2026"),([2026,10],"2026-10"),([2026,10,3],"2026-10-03")]:
            self.assertEqual(cli.crossref_date({'published':{'date-parts':[parts]}})[0],expected)
        self.assertEqual(cli.crossref_date({}),(None,"unknown"))
        with self.assertRaises(ValueError): cli.crossref_date({'published':{'date-parts':[[2026,2,30]]}})

    def test_cross_disciplinary_filter_and_issn_boundary(self):
        unrelated=record();unrelated['title']=['A protein structure'];unrelated['abstract']='Molecular biology.'
        self.assertEqual(cli.parse({**SOURCE,'scope':'cross'},response([unrelated])[0]),[])
        self.assertEqual(len(cli.parse({**SOURCE,'scope':'cross'},response([record()])[0])),1)
        wrong=record();wrong['ISSN']=['0000-0000']
        with self.assertRaises(ValueError): cli.parse(SOURCE,response([wrong])[0])
        for url in ['https://api.crossref.org/works','https://api.crossref.org/journals/no/works','https://user:secret@api.crossref.org/journals/2470-9476/works']:
            with self.assertRaises(ValueError): cli.valid_url(url,fetch=True)

    def test_explicit_preprint_relation_only(self):
        item=record();item['relation']={'has-preprint':[{'id':'10.48550/arXiv.2401.12345','id-type':'doi'}]}
        result=cli.parse(SOURCE,response([item])[0])[0]
        self.assertEqual(result['identifiers']['arxiv'],'2401.12345')
        self.assertNotIn('arxiv',cli.parse(SOURCE,response([record()])[0])[0]['identifiers'])

    def test_cover_records_and_biomedical_noise_are_excluded(self):
        for title in ['Front Cover', 'Table of Contents', 'Editorial Board']:
            item=record();item['title']=[title]
            self.assertEqual(cli.parse(SOURCE,response([item])[0]),[])
        item=record();item['title']=['Immune response in kidney disease'];item['abstract']='Cellular therapies and antibody responses.'
        self.assertEqual(cli.parse({**SOURCE,'scope':'cross'},response([item])[0]),[])

    def test_hardware_and_control_titles_link_to_map_without_abstracts(self):
        for title,node in [('A soft pneumatic actuator','actuation'),('Adaptive control of robot manipulators','feedback_control'),('A tactile pressure sensor','tactile_sensors'),('A vibrotactile haptic interface','intent_communication')]:
            item=record();item['title']=[title];item.pop('abstract')
            parsed=cli.parse(SOURCE,response([item])[0])[0]
            self.assertIn(node,cli.routing(parsed,cli.framework())[0])

    def test_pagination_is_not_cut_off_at_200(self):
        calls=[]
        def fetch(url):
            calls.append(url)
            return response([record(f'10.1234/{len(calls)}-{i}') for i in range(200 if len(calls)==1 else 3)],'next-page')
        raw,meta=cli.fetch_crossref(SOURCE,fetcher=fetch,sleeper=lambda _:None)
        self.assertEqual(len(cli.parse(SOURCE,raw)),203)
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(calls[1]).query)['cursor'],['next-page'])
        self.assertIn('from-update-date',meta['final_url'])

    def test_broken_pagination_does_not_look_successful(self):
        with self.assertRaises(ValueError):
            cli.fetch_crossref(SOURCE,fetcher=lambda url:response([record()]*200,'same'),sleeper=lambda _:None)

    def test_retry_rate_limit(self):
        calls=[];waits=[]
        def fetch(url):
            calls.append(url)
            if len(calls)==1: raise urllib.error.HTTPError(url,429,'Limited',{'Retry-After':'2'},None)
            return response([])
        cli.fetch_crossref(SOURCE,fetcher=fetch,sleeper=waits.append)
        self.assertEqual(waits,[2])

    def test_publication_links_to_preprint_and_preserves_editorial(self):
        domains=cli.framework()
        preprint=cli.paper_record('Old preprint','tactile robot','2401.12345','v1',doi='10.1234/robot')
        with self.db: cli.upsert(self.db,{'id':'pre','priority':30},preprint,'2026-01-01T00:00:00+00:00',domains)
        wid=self.db.execute('SELECT id FROM works').fetchone()[0]
        cli.edit(self.db,wid,{'title_zh':'人工标题','summary':'人工摘要','commentary':'我的判断'},0)
        for _ in range(2):
            run=cli.ingest_source(self.db,SOURCE,fetcher=lambda s:response([record()]))
            self.assertNotEqual(run['status'],'failed')
        self.assertEqual(self.db.execute('SELECT count(*) FROM works').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM versions').fetchone()[0],2)
        item=cli.export_data(self.db)['items'][0]
        self.assertEqual(item['kind'],'paper')
        self.assertEqual(item['editorial']['summary'],'人工摘要')
        self.assertEqual(item['journal_id'],'science_robotics')
        self.assertEqual(run['new_works'],0)

    def test_failure_keeps_success_checkpoint_and_previous_records(self):
        cli.ingest_source(self.db,SOURCE,fetcher=lambda s:response([record()]))
        previous=self.db.execute("SELECT started_at FROM source_runs WHERE status='updated'").fetchone()[0]
        seen=[]
        def failed(source):
            seen.append(source)
            raise ValueError('Incomplete second page')
        failed_run=cli.ingest_source(self.db,SOURCE,fetcher=failed)
        self.assertEqual(failed_run['status'],'failed')
        self.assertEqual(self.db.execute('SELECT count(*) FROM works').fetchone()[0],1)
        self.assertLess(seen[0]['_since'],previous)
        last=self.db.execute("SELECT started_at FROM source_runs WHERE status IN ('updated','no_change') ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual(last,previous)

    def test_fingerprint_changes_with_evidence_and_empty_window_succeeds(self):
        a=record();b=copy.deepcopy(a);b['abstract']='Changed evidence on robot manipulation.'
        self.assertNotEqual(cli.parse(SOURCE,response([a])[0])[0]['source_fingerprint'],cli.parse(SOURCE,response([b])[0])[0]['source_fingerprint'])
        run=cli.ingest_source(self.db,SOURCE,fetcher=lambda s:response([]))
        self.assertEqual(run['status'],'no_change')


if __name__=='__main__': unittest.main()
