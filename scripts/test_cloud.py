import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
import build_public
import public_state


class CloudBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.db_path=self.base/"research.sqlite"
        with public_state.cli.connect(self.db_path) as db:
            db.execute("CREATE TABLE private_notes(body TEXT)")
            db.execute("INSERT INTO private_notes VALUES('PRIVATE_NOTE_SENTINEL')")

    def tearDown(self):
        self.temp.cleanup()

    def test_public_checkpoint_round_trip_excludes_private_table(self):
        state=public_state.snapshot(self.db_path)
        raw=public_state.serialized(state)
        self.assertNotIn(b"PRIVATE_NOTE_SENTINEL",raw)
        restored=self.base/"restored.sqlite"
        public_state.restore(raw,restored)
        with sqlite3.connect(restored) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='private_notes'").fetchone())
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")

    def test_unknown_checkpoint_table_fails_without_replacing_existing_db(self):
        state=public_state.snapshot(self.db_path)
        state["tables"]["private_notes"]=[{"body":"do not import"}]
        before=self.db_path.read_bytes()
        with self.assertRaises(ValueError):
            public_state.restore(public_state.serialized(state),self.db_path)
        self.assertEqual(before,self.db_path.read_bytes())

    def test_checkpoint_persists_and_restores_from_separate_git_branch(self):
        remote=self.base/"remote.git"
        repo=self.base/"repo"
        subprocess.run(["git","init","--bare",str(remote)],check=True,capture_output=True)
        subprocess.run(["git","init",str(repo)],check=True,capture_output=True)
        subprocess.run(["git","remote","add","origin",str(remote)],cwd=repo,check=True,capture_output=True)
        (repo/"pipeline").mkdir()
        (repo/"pipeline/bootstrap-state.json").write_bytes(public_state.serialized(public_state.snapshot(self.db_path)))
        cloud_db=repo/"pipeline/.data/research.sqlite"
        first=public_state.pull(cloud_db,repo)
        self.assertEqual(first["restored_from"],"reviewed_public_bootstrap")
        saved=public_state.push(cloud_db,repo)
        self.assertEqual(saved["saved_branch"],"helm/public-state")
        cloud_db.unlink()
        again=public_state.pull(cloud_db,repo)
        self.assertEqual(again["head"],saved["commit"])
        files=public_state.git("ls-tree","--name-only",saved["commit"],cwd=repo).decode().splitlines()
        self.assertEqual(files,["research-state.json"])

    def test_build_only_allowlisted_assets_and_public_projection(self):
        root=self.base/"project"
        for folder in ("app","content","方案",".runtime","pipeline"):
            (root/folder).mkdir(parents=True)
        for name in build_public.ASSETS:
            (root/"app"/name).write_text("public asset")
        (root/"app/server.py").write_text("PRIVATE_BACKEND_SENTINEL")
        (root/"app/.private").write_text("PRIVATE_FILE_SENTINEL")
        (root/".runtime/private.sqlite").write_text("PRIVATE_NOTE_SENTINEL")
        (root/"方案/知识框架.json").write_text(json.dumps({"nodes":[{"id":"perception"}],"learning":[{"goal":"PRIVATE_GOAL_SENTINEL"}]}))
        (root/"content/domains.json").write_text('[{"id":"perception","intro":"public intro"}]')
        (root/"content/reviews.json").write_text('[]')
        (root/"content/abstract-briefs.json").write_text('[{"id":"abstract-1"}]')
        (root/"content/domain-overrides.json").write_text(json.dumps({"schema_version":1,"overrides":[{"node_id":"perception","intro":"public correction","revision":1,"updated_at":"2026-11-08"}]}))
        feed=root/"pipeline/public-feed.json"
        feed.write_text('{"schema_version":1,"items":[],"sources":[],"counts":{"works":0}}')
        destination=self.base/"site"
        build_public.build(root,destination,feed)
        text=(destination/"public-data.js").read_text()
        self.assertTrue(text.startswith("window.ATLAS_PUBLIC="))
        self.assertNotIn("PRIVATE_",text)
        self.assertIn("public correction",text)
        self.assertIn('"briefs":[{"id":"abstract-1"}]',text)
        self.assertFalse((destination/"server.py").exists())
        self.assertFalse((destination/".runtime").exists())
        self.assertFalse((destination/".private").exists())

    def test_domain_overrides_reject_identity_fields(self):
        with self.assertRaises(ValueError):
            build_public.apply_domain_overrides([],{"schema_version":1,"overrides":[{"node_id":"perception","intro":"x","revision":1,"updated_at":"today","author_id":"private"}]},[{"id":"perception"}])

    def test_public_data_escapes_script_delimiters(self):
        path=self.base/"data.js"
        build_public.write_data(path,{"text":"</script><script>alert(1)</script>"})
        self.assertNotIn("</script>",path.read_text())


if __name__=="__main__":
    unittest.main()
