#!/usr/bin/env python3
"""Zero-model, standard-library research metadata adapter; no cloud is configured."""
import argparse
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import importlib.util
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DEFAULT_DB = BASE / ".data" / "research.sqlite"
MAX_BYTES = 3_000_000
MAX_ITEMS = 200
HOSTS = {"export.arxiv.org", "rss.arxiv.org", "api.github.com", "huggingface.co"}
LINK_HOSTS = HOSTS | {"arxiv.org", "github.com", "doi.org"}
ALLOWED_PATHS = {
    "export.arxiv.org": {"/api/query"}, "rss.arxiv.org": {"/rss/cs.RO"},
    "api.github.com": {"/repos/huggingface/lerobot/releases"},
    "huggingface.co": {"/api/models/openvla/openvla-7b"},
}
# Reuse the first-phase official-source validators, without editing or executing its main().
spec = importlib.util.spec_from_file_location("source_checks", ROOT / "核查/check_public_sources.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
ATOM = checks.ATOM
text_of = checks.text_of


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value, limit=16000):
    parser = PlainText()
    parser.feed(str(value or ""))
    return html.unescape(" ".join(parser.parts)).strip()[:limit]


def valid_url(url, *, fetch=False):
    p = urllib.parse.urlsplit(url)
    if p.scheme == "http" and p.hostname == "arxiv.org" and not fetch:
        url = "https://" + url[len("http://"):]
        p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Only credential-free HTTPS source URLs are allowed")
    if p.hostname not in (HOSTS if fetch else LINK_HOSTS):
        raise ValueError("Source host is not allowlisted")
    if fetch and p.path not in ALLOWED_PATHS[p.hostname]:
        raise ValueError("Source endpoint is not allowlisted")
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        valid_url(newurl, fetch=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_source(source):
    url = valid_url(source["url"], fetch=True)
    req = urllib.request.Request(url, headers={
        "User-Agent": "EmbodiedAtlasMetadata/0.1 (public research metadata; no model calls)",
        "Accept": "application/atom+xml, application/rss+xml, application/json",
    })
    # Do not inherit shell proxy credentials or machine-specific proxy configuration.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect())
    with opener.open(req, timeout=30) as response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Metadata response exceeds 3 MB limit")
        return raw, {"http_status": response.status, "bytes": len(raw),
                     "sha256": hashlib.sha256(raw).hexdigest(), "final_url": response.url}


def arxiv_identifier(value):
    value = re.sub(r"^arxiv:\s*", "", str(value or "").strip(), flags=re.I)
    value = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", value)
    value = value.removesuffix(".pdf")
    match = re.fullmatch(r"((?:\d{4}\.\d{4,5})|(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(v\d+)?", value)
    if not match:
        raise ValueError("Invalid arXiv identifier")
    return match.group(1), match.group(2)


def doi_identifier(value):
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(value or "").strip(), flags=re.I).lower()
    if not value:
        return None
    if not re.fullmatch(r"10\.\d{4,9}/\S+", value):
        raise ValueError("Invalid DOI")
    return value


def paper_record(title, summary, aid, version, published=None, updated=None, doi=None, **extra):
    return {"kind": "preprint", "title": plain(title, 1000), "title_en": plain(title, 1000),
            "url": "https://arxiv.org/abs/" + aid + (version or ""),
            "summary": plain(summary), "arxiv_id": aid, "doi": doi,
            "identifiers": {"arxiv": aid, **({"doi": doi} if doi else {})},
            "version": version, "published_at": published, "version_updated_at": updated,
            "publication_date_basis": "arxiv_first_submission" if published else "unknown",
            "evidence_scope": "abstract_and_metadata_only", "claims_status": "author_claims_not_independently_reproduced",
            **extra}


def parse(source, raw):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("XML declarations with external entities are not accepted")
    kind = source["parser"]
    result = []
    if kind == "arxiv_api":
        root = ET.fromstring(raw)
        if root.tag != "{http://www.w3.org/2005/Atom}feed":
            raise ValueError("Expected an Atom feed")
        for item in root.findall("a:entry", ATOM)[:MAX_ITEMS]:
            aid, version = arxiv_identifier(text_of(item, "a:id", ATOM))
            doi = doi_identifier(text_of(item, "{http://arxiv.org/schemas/atom}doi"))
            result.append(paper_record(text_of(item, "a:title", ATOM), text_of(item, "a:summary", ATOM),
                aid, version, text_of(item, "a:published", ATOM), text_of(item, "a:updated", ATOM), doi,
                authors=[plain(text_of(x, "a:name", ATOM), 200) for x in item.findall("a:author", ATOM)],
                categories=[x.get("term") for x in item.findall("a:category", ATOM)]))
    elif kind == "arxiv_rss":
        checks.parse_arxiv_rss(raw)
        for item in ET.fromstring(raw).findall("./channel/item")[:MAX_ITEMS]:
            aid, version = arxiv_identifier(text_of(item, "link"))
            result.append(paper_record(text_of(item, "title"), text_of(item, "description"), aid, version,
                feed_announced_at=text_of(item, "pubDate"),
                date_note="RSS announcement is not the original submission date; API verification is pending."))
    elif kind == "github":
        checks.parse_github(raw)
        for item in json.loads(raw)[:MAX_ITEMS]:
            if item.get("draft"):
                continue
            if not item.get("id") or not item.get("tag_name"):
                raise ValueError("Release lacks id or tag")
            result.append({"kind": "code_release", "title": "LeRobot · " + plain(item.get("name") or item["tag_name"], 1000),
                "title_en": plain(item.get("name") or item["tag_name"], 1000),
                "url": valid_url(item["html_url"]), "summary": plain(item.get("body")),
                "identifiers": {"github_release": str(item["id"])}, "arxiv_id": None, "doi": None,
                "version": item["tag_name"], "published_at": item.get("published_at"),
                "publication_date_basis": "github_release_published_at", "version_updated_at": item.get("updated_at"),
                "evidence_scope": "official_release_notes_only", "claims_status": "maintainer_claims_not_independently_reproduced",
                "prerelease": bool(item.get("prerelease")), "fixed_domains": ["data", "reproducible_systems", "learning"]})
    elif kind == "huggingface":
        checks.parse_huggingface(raw)
        item = json.loads(raw)
        if item.get("private") or item.get("id") != "openvla/openvla-7b":
            raise ValueError("Only the configured public OpenVLA model is accepted")
        if not re.fullmatch(r"[a-f0-9]{40,64}", item.get("sha", "")):
            raise ValueError("Model metadata lacks an immutable revision")
        result.append({"kind": "model_repository", "title": "OpenVLA · openvla-7b 模型仓库", "title_en": item["id"],
            "url": "https://huggingface.co/" + item["id"], "summary": "官方模型仓库元数据；本次未下载权重、模型卡全文或运行模型。版本以仓库提交 SHA 标记，不据此推断性能提升。",
            "identifiers": {"huggingface_model": item["id"]}, "arxiv_id": None, "doi": None,
            "version": item["sha"], "published_at": None, "repository_created_at": item.get("createdAt"),
            "publication_date_basis": "unknown_repository_created_at_is_not_release_date",
            "version_updated_at": item.get("lastModified"), "gated": item.get("gated", False),
            "evidence_scope": "repository_metadata_only", "claims_status": "metadata_only_no_performance_claims",
            "fixed_domains": ["learning", "vla", "data", "reproducible_systems"]})
    else:
        raise ValueError("Unsupported parser")
    for item in result:
        valid_url(item["url"])
        if not item["title"]:
            raise ValueError("Entry has no title")
    return result


# Transparent lexical routing: these rules are not a measure of scientific quality.
RULES = {
    "tactile_perception": ("tactile", "touch sensing", "触觉"),
    "multimodal_fusion": ("multimodal", "multi-modal", "visuo-tactile", "多模态"),
    "visual_perception": ("visual perception", "object detection", "3d perception", "pose estimation"),
    "state_estimation": ("state estimation", "kalman", "localization"),
    "spatial_mapping": ("slam", "mapping", "spatial memory"),
    "predictive_dynamics": ("world model", "dynamics model", "predictive model"),
    "task_decomposition": ("task planning", "long-horizon", "task decomposition"),
    "navigation_planning": ("navigation", "path planning", "motion planning"),
    "language_grounding": ("language instruction", "language grounding"),
    "locomotion": ("locomotion", "legged", "quadruped", "humanoid"),
    "dexterous_manipulation": ("manipulation", "grasp", "dexterous"),
    "contact_control": ("contact-rich", "force control", "impedance"),
    "feedback_control": ("model predictive control", "mpc", "optimal control"),
    "imitation_learning": ("imitation learning", "behavior cloning", "behaviour cloning"),
    "reinforcement_learning": ("reinforcement learning",),
    "self_supervised": ("self-supervised", "self supervised"),
    "vla": ("vision-language-action", "vision language action", "openvla", "vla"),
    "adaptation": ("adaptation", "transfer learning", "generalization"),
    "demonstration_collection": ("demonstration", "teleoperation", "robot dataset", "droid"),
    "reproducible_systems": ("open-source", "open source", "lerobot"),
    "physics_simulation": ("simulator", "simulation", "physics engine"),
    "sim_to_real": ("sim-to-real", "sim2real"),
    "human_evaluation": ("human-robot", "human robot", "user study"),
    "benchmarks": ("benchmark", "evaluation protocol"),
    "robot_safety": ("robot safety", "safe control", "safety constraint"),
}
METHODS = {"diffusion_policy": ("diffusion policy", "diffusion policies"),
           "vla": ("vision-language-action", "openvla"),
           "reinforcement_learning": ("reinforcement learning",),
           "imitation_learning": ("imitation learning", "behavior cloning")}


def contains(text, keyword):
    if keyword.isascii() and keyword.replace("-", "").replace(" ", "").isalnum():
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])", text))
    return keyword in text


def routing(item, domains):
    text = (item["title"] + " " + item["summary"]).lower()
    matches = {key: [word for word in words if contains(text, word)] for key, words in RULES.items()}
    matches = {key: words for key, words in matches.items() if words}
    for key in item.get("fixed_domains", []):
        matches.setdefault(key, ["official_repository_scope"])
    # Ambiguous entries stay unclassified instead of inventing a new field.
    for key in list(matches):
        parent = domains[key].get("parent_id")
        if parent:
            matches.setdefault(parent, ["child:" + key])
    methods = [key for key, words in METHODS.items() if any(contains(text, word) for word in words)]
    score = min(100, 20 + 10 * min(len([x for x in matches if domains[x].get("parent_id")]), 4)
                + (20 if item.get("summary") else 0) + (10 if item.get("version") else 0))
    reason = "规则排序（非 AI、非质量评审）：官方来源 +20；有摘要/发行说明 +20；明确版本 +10；每个匹配子领域 +10（最多 +40）。"
    return matches, methods, score, reason


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS works(id TEXT PRIMARY KEY,kind TEXT NOT NULL,data TEXT NOT NULL,priority INTEGER NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,content_changed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS identifiers(kind TEXT NOT NULL,value TEXT NOT NULL,work_id TEXT NOT NULL REFERENCES works(id),PRIMARY KEY(kind,value));
CREATE TABLE IF NOT EXISTS versions(work_id TEXT NOT NULL REFERENCES works(id),version TEXT NOT NULL,data TEXT NOT NULL,content_hash TEXT NOT NULL,priority INTEGER NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,PRIMARY KEY(work_id,version));
CREATE TABLE IF NOT EXISTS version_changes(id INTEGER PRIMARY KEY,work_id TEXT NOT NULL,version TEXT NOT NULL,old_data TEXT NOT NULL,replaced_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS work_sources(work_id TEXT NOT NULL REFERENCES works(id),source_id TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,PRIMARY KEY(work_id,source_id));
CREATE TABLE IF NOT EXISTS work_domains(work_id TEXT NOT NULL REFERENCES works(id),domain_id TEXT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(work_id,domain_id));
CREATE TABLE IF NOT EXISTS editorial(work_id TEXT PRIMARY KEY REFERENCES works(id),title_zh TEXT,summary TEXT,commentary TEXT,revision INTEGER NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_runs(id INTEGER PRIMARY KEY,source_id TEXT NOT NULL,url TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,status TEXT NOT NULL,http_status INTEGER,items_seen INTEGER DEFAULT 0,new_works INTEGER DEFAULT 0,new_versions INTEGER DEFAULT 0,changed_records INTEGER DEFAULT 0,error TEXT,response_sha256 TEXT);
CREATE INDEX IF NOT EXISTS source_runs_by_source ON source_runs(source_id,id);
"""


def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def framework():
    return {x["id"]: x for x in json.loads((ROOT / "方案/知识框架.json").read_text())["nodes"]}


def sources():
    entries = json.loads((BASE / "sources.json").read_text())
    if len({x["id"] for x in entries}) != len(entries):
        raise ValueError("Duplicate source IDs")
    for entry in entries:
        valid_url(entry["url"], fetch=True)
    return entries


def upsert(db, source, item, stamp, domains):
    matches = {row[0] for key, value in item["identifiers"].items()
               for row in db.execute("SELECT work_id FROM identifiers WHERE kind=? AND value=?", (key, value))}
    if len(matches) > 1:
        raise ValueError("Identifier conflict requires manual review; no works were merged")
    wid = next(iter(matches)) if matches else "work_" + digest(item["identifiers"])[:20]
    previous = db.execute("SELECT * FROM works WHERE id=?", (wid,)).fetchone()
    linked, methods, score, reason = routing(item, domains)
    item = {**item, "method_tags": methods, "rule_score": score, "selection_reason": reason}
    version_key = item["version"] or "unspecified"
    prior_version = db.execute("SELECT * FROM versions WHERE work_id=? AND version=?", (wid, version_key)).fetchone()
    payload = encode(item)
    prefer_version = prior_version is None or source["priority"] >= prior_version["priority"]
    version_changed = prior_version is not None and prefer_version and prior_version["content_hash"] != digest(item)
    new_work = previous is None
    new_version = prior_version is None
    if new_work:
        db.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?)", (wid,item["kind"],payload,source["priority"],stamp,stamp,stamp))
    else:
        old = json.loads(previous["data"])
        old_time = old.get("version_updated_at") or old.get("published_at") or ""
        new_time = item.get("version_updated_at") or item.get("published_at") or ""
        prefer = source["priority"] > previous["priority"] or (source["priority"] == previous["priority"] and new_time >= old_time)
        changed = prefer and payload != previous["data"]
        db.execute("UPDATE works SET data=?,priority=?,last_seen_at=?,content_changed_at=? WHERE id=?",
            (payload if prefer else previous["data"],source["priority"] if prefer else previous["priority"],stamp,
             stamp if changed or new_version or version_changed else previous["content_changed_at"],wid))
    for key, value in item["identifiers"].items():
        db.execute("INSERT OR IGNORE INTO identifiers VALUES(?,?,?)", (key,value,wid))
    if version_changed:
        db.execute("INSERT INTO version_changes(work_id,version,old_data,replaced_at) VALUES(?,?,?,?)",
                   (wid,version_key,prior_version["data"],stamp))
    if prefer_version:
        db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?) ON CONFLICT(work_id,version) DO UPDATE SET data=excluded.data,content_hash=excluded.content_hash,priority=excluded.priority,last_seen_at=excluded.last_seen_at",
                   (wid,version_key,payload,digest(item),source["priority"],stamp,stamp))
    db.execute("INSERT INTO work_sources VALUES(?,?,?,?) ON CONFLICT(work_id,source_id) DO UPDATE SET last_seen_at=excluded.last_seen_at", (wid,source["id"],stamp,stamp))
    # Relations use the best available record, preventing RSS from weakening API routing.
    best = json.loads(db.execute("SELECT data FROM works WHERE id=?", (wid,)).fetchone()[0])
    best_domains = routing(best, domains)[0]
    db.execute("DELETE FROM work_domains WHERE work_id=?", (wid,))
    db.executemany("INSERT INTO work_domains VALUES(?,?,?)", [(wid,key,encode(why)) for key,why in best_domains.items()])
    return int(new_work), int(new_version), int(version_changed)


def ingest_source(db, source, fetcher=fetch_source, domains=None):
    started = now()
    cursor = db.execute("INSERT INTO source_runs(source_id,url,started_at,status) VALUES(?,?,?,'running')", (source["id"],source["url"],started))
    run_id = cursor.lastrowid
    db.commit()
    meta = {}
    try:
        raw, meta = fetcher(source)
        items = parse(source, raw)
        totals = [0,0,0]
        with db:
            for item in items:
                counts = upsert(db, source, item, started, domains or framework())
                totals = [a+b for a,b in zip(totals,counts)]
            status = "updated" if any(totals) else "no_change"
            db.execute("UPDATE source_runs SET finished_at=?,status=?,http_status=?,items_seen=?,new_works=?,new_versions=?,changed_records=?,response_sha256=? WHERE id=?",
                (now(),status,meta.get("http_status"),len(items),*totals,meta.get("sha256"),run_id))
    except Exception as error:
        db.rollback()
        # Avoid exception text containing credentials or unexpected remote payloads.
        status = getattr(error,"code",None)
        message = f"HTTP {status}" if isinstance(error,urllib.error.HTTPError) else f"{type(error).__name__}: {str(error)[:250]}"
        with db:
            db.execute("UPDATE source_runs SET finished_at=?,status='failed',http_status=?,error=? WHERE id=?", (now(),status or meta.get("http_status"),message,run_id))
    return dict(db.execute("SELECT * FROM source_runs WHERE id=?", (run_id,)).fetchone())


class RevisionConflict(Exception):
    pass


def edit(db, wid, patch, expected):
    allowed = {"summary", "commentary", "title_zh"}
    if not isinstance(patch,dict) or not patch or not set(patch) <= allowed:
        raise ValueError("Edit requires summary, commentary or title_zh fields only")
    if any(not isinstance(v,str) or len(v)>20000 for v in patch.values()):
        raise ValueError("Editorial fields must be strings of at most 20,000 characters")
    with db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM works WHERE id=?", (wid,)).fetchone():
            raise ValueError("Unknown work ID")
        old = db.execute("SELECT * FROM editorial WHERE work_id=?", (wid,)).fetchone()
        revision = old["revision"] if old else 0
        if revision != expected:
            raise RevisionConflict(f"Expected revision {expected}; current revision is {revision}")
        values = {k:old[k] if old else None for k in allowed}
        values.update(patch)
        stamp = now()
        db.execute("INSERT INTO editorial VALUES(?,?,?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET title_zh=excluded.title_zh,summary=excluded.summary,commentary=excluded.commentary,revision=excluded.revision,updated_at=excluded.updated_at",
            (wid,values["title_zh"],values["summary"],values["commentary"],revision+1,stamp))
        db.execute("UPDATE works SET content_changed_at=? WHERE id=?",(stamp,wid))
    return {"id":wid,"revision":revision+1,"updated_at":stamp,**values}


def export_data(db):
    result = {"schema_version":1,"generated_at":now(),"mode":"local_verified_adapter","model_calls":0,
        "automatic_interpretation":False,"schedule_status":"not_deployed","sources":[],"items":[],"domain_updates":{}}
    for source in sources():
        run = db.execute("SELECT * FROM source_runs WHERE source_id=? ORDER BY id DESC LIMIT 1", (source["id"],)).fetchone()
        success = db.execute("SELECT finished_at FROM source_runs WHERE source_id=? AND status IN ('updated','no_change') ORDER BY id DESC LIMIT 1", (source["id"],)).fetchone()
        entry = {k:source[k] for k in ("id","name","url")}
        entry.update({"last_attempt_at":run["started_at"] if run else None,"last_success_at":success[0] if success else None,
                      "last_status":run["status"] if run else "never_checked"})
        entry.update({k:run[k] if run else None for k in ("items_seen","new_works","new_versions","changed_records","error")})
        result["sources"].append(entry)
    for work in db.execute("SELECT * FROM works ORDER BY content_changed_at DESC,id"):
        data = json.loads(work["data"])
        # Explicit allowlist: never serialize arbitrary database tables or private records.
        keys = ("kind","title","title_en","url","summary","doi","arxiv_id","published_at","publication_date_basis",
                "version","version_updated_at","method_tags","rule_score","selection_reason","evidence_scope","claims_status",
                "authors","categories","prerelease","repository_created_at","date_note")
        item = {k:data.get(k) for k in keys}
        item.update({"id":work["id"],"first_seen_at":work["first_seen_at"],"last_seen_at":work["last_seen_at"],"content_changed_at":work["content_changed_at"]})
        item["domain_ids"] = [r[0] for r in db.execute("SELECT domain_id FROM work_domains WHERE work_id=? ORDER BY domain_id", (work["id"],))]
        item["classification_status"] = "rule_mapped_needs_human_judgment" if item["domain_ids"] else "needs_classification"
        item["domain_mapping_basis"] = {r[0]:json.loads(r[1]) for r in db.execute("SELECT domain_id,reason FROM work_domains WHERE work_id=?",(work["id"],))}
        item["source_ids"] = [r[0] for r in db.execute("SELECT source_id FROM work_sources WHERE work_id=? ORDER BY source_id", (work["id"],))]
        item["versions"] = [{"version":r["version"],"url":json.loads(r["data"])["url"],"updated_at":json.loads(r["data"]).get("version_updated_at"),"first_seen_at":r["first_seen_at"]}
                            for r in db.execute("SELECT * FROM versions WHERE work_id=? ORDER BY first_seen_at,version", (work["id"],))]
        editorial = db.execute("SELECT * FROM editorial WHERE work_id=?", (work["id"],)).fetchone()
        item["editorial"] = {k:editorial[k] for k in ("summary","commentary","title_zh","revision","updated_at")} if editorial else None
        result["items"].append(item)
        for domain in item["domain_ids"]:
            update = result["domain_updates"].setdefault(domain,{"count":0,"last_content_change_at":work["content_changed_at"]})
            update["count"] += 1
            update["last_content_change_at"] = max(update["last_content_change_at"],work["content_changed_at"])
    result["counts"] = {"works":len(result["items"]),"versions":db.execute("SELECT count(*) FROM versions").fetchone()[0],"sources":len(result["sources"])}
    return result


def search(db, query, domain=None, limit=50):
    if not query.strip() or len(query)>200:
        raise ValueError("Search query must be 1–200 characters")
    # instr implements literal substring matching: %, _ and SQL text cannot become operators.
    rows = db.execute("""SELECT w.id FROM works w LEFT JOIN editorial e ON e.work_id=w.id
        WHERE (instr(lower(json_extract(w.data,'$.title')),lower(?))>0
        OR instr(lower(json_extract(w.data,'$.summary')),lower(?))>0
        OR instr(lower(COALESCE(e.title_zh,'')),lower(?))>0
        OR instr(lower(COALESCE(e.summary,'')),lower(?))>0)
        AND (? IS NULL OR EXISTS(SELECT 1 FROM work_domains d WHERE d.work_id=w.id AND d.domain_id=?))
        ORDER BY w.content_changed_at DESC LIMIT ?""", (query,query,query,query,domain,domain,limit))
    ids = {r[0] for r in rows}
    return [x for x in export_data(db)["items"] if x["id"] in ids]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command",required=True)
    ingestion = sub.add_parser("ingest")
    ingestion.add_argument("--source",action="append",help="Limit to source IDs (repeatable)")
    sub.add_parser("check",help="Read persisted run status and SQLite integrity; no network")
    exp = sub.add_parser("export")
    exp.add_argument("--out",type=Path)
    query = sub.add_parser("search")
    query.add_argument("query")
    query.add_argument("--domain")
    editing = sub.add_parser("edit")
    editing.add_argument("work_id")
    editing.add_argument("--expected-revision",type=int,required=True)
    editing.add_argument("--json-stdin",action="store_true",required=True)
    args = parser.parse_args()
    exit_code = 0
    with connect(args.db) as db:
        if args.command == "ingest":
            selected = sources()
            if args.source:
                unknown = set(args.source)-{x["id"] for x in selected}
                if unknown:
                    raise ValueError("Unknown source IDs")
                selected = [x for x in selected if x["id"] in args.source]
            runs = []
            last_arxiv = None
            for source in selected:
                if "arxiv.org" in source["url"] and last_arxiv is not None:
                    time.sleep(max(0,3.1-(time.monotonic()-last_arxiv)))
                runs.append(ingest_source(db,source))
                if "arxiv.org" in source["url"]:
                    last_arxiv = time.monotonic()
            result = {"runs":runs,"counts":export_data(db)["counts"],"model_calls":0,"cloud_schedule":"not_deployed"}
            exit_code = int(any(x["status"] == "failed" for x in runs))
        elif args.command == "check":
            data = export_data(db)
            result = {"integrity":db.execute("PRAGMA integrity_check").fetchone()[0],"counts":data["counts"],"sources":data["sources"],"cloud_schedule":"not_deployed"}
        elif args.command == "export":
            result = export_data(db)
            if args.out:
                args.out.parent.mkdir(parents=True,exist_ok=True)
                tmp = args.out.with_suffix(args.out.suffix+".tmp")
                tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
                tmp.replace(args.out)
                result = {"exported":str(args.out),"counts":result["counts"],"private_data_included":False}
        elif args.command == "search":
            result = {"query":args.query,"items":search(db,args.query,args.domain)}
        else:
            content = sys.stdin.read(100_001)
            if len(content)>100_000:
                raise ValueError("Editorial input too large")
            result = edit(db,args.work_id,json.loads(content),args.expected_revision)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RevisionConflict as error:
        print(json.dumps({"error":"revision_conflict","detail":str(error)},ensure_ascii=False))
        sys.exit(3)
    except (ValueError,sqlite3.Error,OSError) as error:
        print(json.dumps({"error":type(error).__name__,"detail":str(error)},ensure_ascii=False))
        sys.exit(2)
