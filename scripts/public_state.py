#!/usr/bin/env python3
"""Persist only allowlisted public database rows on a separate Git branch."""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"pipeline"))
import cli

BRANCH="helm/public-state"
FILENAME="research-state.json"
LIMIT=50_000_000
TABLES={
    "works":("id","kind","data","priority","first_seen_at","last_seen_at","content_changed_at"),
    "identifiers":("kind","value","work_id"),
    "versions":("work_id","version","data","content_hash","priority","first_seen_at","last_seen_at"),
    "version_changes":("id","work_id","version","old_data","replaced_at"),
    "work_sources":("work_id","source_id","first_seen_at","last_seen_at"),
    "work_domains":("work_id","domain_id","reason"),
    "editorial":("work_id","title_zh","summary","commentary","revision","updated_at"),
    "source_runs":("id","source_id","url","started_at","finished_at","status","http_status","items_seen","new_works","new_versions","changed_records","error","response_sha256"),
}


def snapshot(path):
    # Never create a missing input DB: a missing state must not silently become empty.
    if not path.is_file() or path.is_symlink():
        raise ValueError("Public database is missing or is a symlink")
    conn=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)
    conn.row_factory=sqlite3.Row
    try:
        conn.execute("BEGIN")
        result={"schema_version":1,"public_only":True,"tables":{}}
        for table,columns in TABLES.items():
            order=",".join(columns[:2])
            sql=f"SELECT {','.join(columns)} FROM {table}"
            if table=="source_runs":
                # Keep roughly 100 daily checks per source; previous Git snapshots retain older history.
                sql+=" WHERE id IN (SELECT id FROM (SELECT id,ROW_NUMBER() OVER(PARTITION BY source_id ORDER BY id DESC) AS n FROM source_runs) WHERE n<=100)"
            result["tables"][table]=[dict(row) for row in conn.execute(sql+f" ORDER BY {order}")]
        return result
    finally:
        conn.close()


def serialized(state):
    raw=(json.dumps(state,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode()
    if len(raw)>LIMIT:
        raise ValueError("Public checkpoint exceeds 50 MB; migrate storage before continuing")
    return raw


def restore(raw,path):
    if len(raw)>LIMIT:
        raise ValueError("Checkpoint exceeds 50 MB")
    state=json.loads(raw)
    if set(state)!={"schema_version","public_only","tables"} or state["schema_version"]!=1 or state["public_only"] is not True or set(state["tables"])!=set(TABLES):
        raise ValueError("Unknown checkpoint schema or tables")
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,suffix=".sqlite",delete=False) as temporary:
        fresh=Path(temporary.name)
    try:
        db=cli.connect(fresh)
        try:
            with db:
                for table,columns in TABLES.items():
                    rows=state["tables"][table]
                    if not isinstance(rows,list) or any(not isinstance(row,dict) or set(row)!=set(columns) for row in rows):
                        raise ValueError("Unknown public checkpoint columns")
                    db.executemany(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", [tuple(row[col] for col in columns) for row in rows])
                if db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("Checkpoint has broken public references")
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise ValueError("Checkpoint integrity failed")
        finally:
            db.close()
        fresh.replace(path)
    finally:
        fresh.unlink(missing_ok=True)


def git(*args,cwd=ROOT,input=None):
    completed=subprocess.run(["git",*args],cwd=cwd,input=input,capture_output=True,check=False)
    if completed.returncode:
        # Avoid printing remote URLs or configured authentication details from Git diagnostics.
        raise RuntimeError("Git operation failed: "+args[0]+"; see repository permissions/state")
    return completed.stdout


def pull(path,repo=ROOT):
    remote=git("ls-remote","--heads","origin","refs/heads/"+BRANCH,cwd=repo).decode().strip()
    head=None
    if remote:
        head=remote.split()[0]
        if not re.fullmatch(r"[a-f0-9]{40,64}",head):
            raise ValueError("Invalid public state ref")
        git("fetch","--depth=1","origin","refs/heads/"+BRANCH,cwd=repo)
        raw=git("show",head+":"+FILENAME,cwd=repo)
    else:
        seed=repo/"pipeline/bootstrap-state.json"
        raw=seed.read_bytes()
    restore(raw,path)
    path.with_suffix(".remote-head").write_text(head or "")
    return {"restored_from":BRANCH if head else "reviewed_public_bootstrap","head":head}


def apply_editorial_file(db_path,patch_path):
    if not patch_path.exists():
        return {"applied":0}
    payload=json.loads(patch_path.read_text())
    if set(payload)!={"schema_version","edits"} or payload["schema_version"]!=1:
        raise ValueError("Invalid public editorial patch schema")
    count=0
    with cli.connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        for row in payload["edits"]:
            if set(row)!=set(TABLES["editorial"]):
                raise ValueError("Unexpected editorial patch fields")
            old=db.execute("SELECT * FROM editorial WHERE work_id=?",(row["work_id"],)).fetchone()
            if old and old["revision"]>row["revision"]:
                continue
            if old and old["revision"]==row["revision"]:
                if any(old[key]!=row[key] for key in ("title_zh","summary","commentary")):
                    raise ValueError("Conflicting public editorial revision; review before overwrite")
                continue
            columns=TABLES["editorial"]
            db.execute("INSERT INTO editorial VALUES(?,?,?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET title_zh=excluded.title_zh,summary=excluded.summary,commentary=excluded.commentary,revision=excluded.revision,updated_at=excluded.updated_at",tuple(row[k] for k in columns))
            db.execute("UPDATE works SET content_changed_at=? WHERE id=?",(row["updated_at"],row["work_id"]))
            count+=1
    return {"applied":count}


def push(path,repo=ROOT):
    marker=path.with_suffix(".remote-head")
    if not marker.is_file():
        raise ValueError("Restore remote state before publishing a checkpoint")
    head=marker.read_text().strip()
    raw=serialized(snapshot(path.resolve()))
    blob=git("hash-object","-w","--stdin",cwd=repo,input=raw).decode().strip()
    tree=git("mktree",cwd=repo,input=f"100644 blob {blob}\t{FILENAME}\n".encode()).decode().strip()
    args=["-c","user.name=Embodied Atlas updater","-c","user.email=actions@users.noreply.github.com","commit-tree",tree]
    if head:
        args.extend(["-p",head])
    commit=git(*args,cwd=repo,input=("Update public research state "+cli.now()+"\n").encode()).decode().strip()
    # No force push: concurrent or manually edited state must fail safely.
    git("push","origin",commit+":refs/heads/"+BRANCH,cwd=repo)
    marker.write_text(commit)
    return {"saved_branch":BRANCH,"commit":commit,"bytes":len(raw)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("snapshot","pull","push","apply-editorial"))
    parser.add_argument("--db",type=Path,default=cli.DEFAULT_DB)
    parser.add_argument("--out",type=Path)
    parser.add_argument("--patch",type=Path,default=ROOT/"pipeline/editorial-overrides.json")
    args=parser.parse_args()
    if args.command=="snapshot":
        state=snapshot(args.db.resolve())
        if not args.out:
            parser.error("snapshot needs --out")
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_bytes(serialized(state))
        result={"snapshot":str(args.out),"public_only":True}
    elif args.command=="pull": result=pull(args.db)
    elif args.command=="push": result=push(args.db)
    else: result=apply_editorial_file(args.db,args.patch)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
