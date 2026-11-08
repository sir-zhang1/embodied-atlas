#!/usr/bin/env python3
"""Build only public assets; never import the local server or open .runtime."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("index.html", "app.js", "styles.css", "mark.svg")
FRAMEWORK_KEYS = ("nodes", "edges", "sources", "evolution")


def read_json(path, default=None):
    if path.is_symlink():
        raise ValueError(f"Symlink input is not permitted: {path.name}")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def apply_domain_overrides(domains, overrides, nodes):
    ids = {x["id"] for x in nodes}
    if overrides.get("schema_version") != 1 or not isinstance(overrides.get("overrides"), list):
        raise ValueError("Invalid public domain override schema")
    by_id = {}
    for row in overrides["overrides"]:
        if set(row) != {"node_id", "intro", "revision", "updated_at"} or row["node_id"] not in ids:
            raise ValueError("Unknown fields or domain in public override")
        if not isinstance(row["intro"], str) or not isinstance(row["revision"], int):
            raise ValueError("Invalid public domain override fields")
        by_id[row["node_id"]] = {k:row[k] for k in ("intro", "revision", "updated_at")}
    return [{**x, "editorial":by_id.get(x["id"])} for x in domains]


def make_data(root, feed_path, cloud=False):
    framework = read_json(root / "方案/知识框架.json", {})
    domains = read_json(root / "content/domains.json", [])
    domains = apply_domain_overrides(domains,
        read_json(root / "content/domain-overrides.json", {"schema_version":1,"overrides":[]}), framework["nodes"])
    feed = read_json(feed_path)
    if not isinstance(feed,dict) or feed.get("schema_version") != 1:
        raise ValueError("A valid public feed export is required")
    data = {"framework":{key:framework.get(key,[]) for key in FRAMEWORK_KEYS}, "domains":domains,
            "reviews":read_json(root / "content/reviews.json", []),
            "briefs":read_json(root / "content/abstract-briefs.json", []),
            "feed":feed, "runtime":"static", "scheduled":False}
    if cloud:
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        event = os.environ.get("GITHUB_EVENT_NAME", "")
        if os.environ.get("GITHUB_ACTIONS") != "true" or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not run_id.isdigit():
            raise ValueError("Cloud metadata requires a real GitHub Actions run")
        data["runtime"] = "cloud"
        data["scheduled"] = event == "schedule"
        data["scheduler"] = {"provider":"github_actions", "configured":True, "last_trigger":event,
            "run_url":f"https://github.com/{repository}/actions/runs/{run_id}",
            "checked_at":dt.datetime.now(dt.timezone.utc).isoformat(), "timezone":"Asia/Taipei", "daily_time":"07:17"}
        feed["mode"] = "github_actions_public_adapter"
        feed["schedule_status"] = "scheduled_run_observed" if event == "schedule" else ("cloud_push_run_observed" if event == "push" else "cloud_manual_run_observed")
    return data


def write_data(path, data):
    # Escape HTML delimiters even though this is an external script asset.
    value = json.dumps(data,ensure_ascii=False,separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text("window.ATLAS_PUBLIC="+value+";\n",encoding="utf-8")
    temporary.replace(path)


def build(root, destination, feed_path, cloud=False):
    root, destination = root.resolve(), destination.resolve()
    if destination == root or destination == root / "app" or (root / ".runtime") == destination or (root / ".runtime") in destination.parents:
        raise ValueError("Choose a separate public build directory, not app or .runtime")
    if destination.exists() and any(destination.iterdir()):
        # Refuse to delete caller files. Existing known artifacts are overwritten below.
        allowed = set(ASSETS) | {"public-data.js","public-feed.json",".nojekyll","build-manifest.json"}
        if any(p.name not in allowed or p.is_symlink() or not p.is_file() for p in destination.iterdir()):
            raise ValueError("Output directory contains non-build files; use a new empty directory")
    destination.mkdir(parents=True,exist_ok=True)
    for name in ASSETS:
        source = root / "app" / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("Missing or symlink public asset: "+name)
        shutil.copyfile(source,destination/name)
    data = make_data(root,feed_path,cloud)
    write_data(destination/"public-data.js",data)
    (destination/"public-feed.json").write_text(json.dumps(data["feed"],ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (destination/".nojekyll").write_text("")
    manifest = {"public_only":True,"runtime":data["runtime"],"scheduled_run_observed":data["scheduled"],
                "counts":data["feed"]["counts"],"files":[]}
    for path in sorted(destination.iterdir()):
        if path.name == "build-manifest.json":
            continue
        raw = path.read_bytes()
        manifest["files"].append({"name":path.name,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})
    if sum(x["bytes"] for x in manifest["files"]) > 20_000_000:
        raise ValueError("Static bundle exceeds the 20 MB project guard; review growth before deploying")
    (destination/"build-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=ROOT)
    parser.add_argument("--feed",type=Path)
    parser.add_argument("--out",type=Path)
    parser.add_argument("--data-js",type=Path,help="Only write the public projection JS, e.g. app/public-data.js")
    parser.add_argument("--cloud",action="store_true")
    args = parser.parse_args()
    if bool(args.out)==bool(args.data_js):
        parser.error("Choose exactly one of --out or --data-js")
    feed = args.feed or args.root / "pipeline/public-feed.json"
    if args.data_js:
        data=make_data(args.root,feed,args.cloud)
        write_data(args.data_js,data)
        result={"written":str(args.data_js),"counts":data["feed"]["counts"],"public_only":True}
    else:
        result=build(args.root,args.out,feed,args.cloud)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
