#!/usr/bin/env python3
"""Four unauthenticated read-only samples; no retries, models, weights, or ingestion."""

import datetime as dt
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


DEST = Path(__file__).with_name("public-source-checks.json")
MAX_BYTES = 2_000_000
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def text_of(node, path, ns=None):
    return node.findtext(path, default="", namespaces=ns) or None


def parse_arxiv_api(raw):
    root = ET.fromstring(raw)
    entries = root.findall("a:entry", ATOM)
    if not entries:
        raise ValueError("Atom parsed but no article entry returned")
    entry = entries[0]
    article_id = text_of(entry, "a:id", ATOM)
    match = re.search(r"v(\d+)$", article_id or "")
    return {
        "format": "Atom", "entries": len(entries),
        "sample": {
            "id": article_id,
            "title": " ".join((text_of(entry, "a:title", ATOM) or "").split()),
            "published": text_of(entry, "a:published", ATOM),
            "updated": text_of(entry, "a:updated", ATOM),
            "version": "v" + match.group(1) if match else None,
        },
    }


def parse_arxiv_rss(raw):
    root = ET.fromstring(raw)
    entries = root.findall("./channel/item")
    if root.tag != "rss":
        raise ValueError("Response is XML but not RSS 2.0")
    sample = None
    if entries:
        entry = entries[0]
        link = text_of(entry, "link")
        version = re.search(r"v(\d+)$", link or "")
        sample = {
            "title": text_of(entry, "title"), "url": link,
            "published": text_of(entry, "pubDate"),
            "updated": None,
            "version": "v" + version.group(1) if version else None,
            "date_caveat": "RSS pubDate alone does not establish the original paper submission date; cross-check arXiv API.",
        }
    return {
        "format": "RSS 2.0", "entries": len(entries),
        "feed_last_build_date": text_of(root, "./channel/lastBuildDate"),
        "sample": sample,
    }


def parse_github(raw):
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("GitHub releases response is not an array")
    sample = None
    if data:
        item = data[0]
        sample = {key: item.get(key) for key in (
            "id", "html_url", "tag_name", "name", "published_at",
            "created_at", "updated_at", "draft", "prerelease", "target_commitish",
        )}
        sample["version"] = item.get("tag_name")
        sample["date_caveat"] = "Use published_at for release publication; created_at is not the publication timestamp."
    return {"format": "JSON", "entries": len(data), "sample": sample}


def parse_huggingface(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or not data.get("id"):
        raise ValueError("Hugging Face response lacks model id")
    return {
        "format": "JSON",
        "sample": {key: data.get(key) for key in (
            "id", "sha", "createdAt", "lastModified", "pipeline_tag", "private", "gated",
        )},
        "version": data.get("sha"),
        "date_caveat": "createdAt is repository creation; lastModified is repository modification. Neither proves paper publication or a named release date.",
        "weights_downloaded": False,
    }


def fetch(name, url, parser):
    result = {"name": name, "url": url, "checked_at_utc": now(), "http_status": None,
              "content_type": None, "parse_ok": False, "ingestion_attempted": False}
    req = urllib.request.Request(url, headers={
        "User-Agent": "EmbodiedKnowledgeFeasibility/0.1 (one-off public metadata check)",
        "Accept": "application/json, application/atom+xml, application/rss+xml, application/xml;q=0.9",
    })
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result["http_status"] = response.status
            result["content_type"] = response.headers.get("Content-Type")
            result["final_url"] = response.url
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("Response exceeds metadata size limit")
            result["bytes"] = len(raw)
            result["sha256"] = hashlib.sha256(raw).hexdigest()
            result["parsed"] = parser(raw)
            result["parse_ok"] = True
    except urllib.error.HTTPError as error:
        result["http_status"] = error.code
        result["content_type"] = error.headers.get("Content-Type")
        result["error"] = f"HTTP {error.code}: {error.reason}"
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def main():
    started = now()
    results = []
    results.append(fetch("arxiv_api_single_paper", "https://export.arxiv.org/api/query?id_list=2406.09246", parse_arxiv_api))
    # arXiv requests deliberately remain sequential with at least three seconds between them.
    time.sleep(3.1)
    results.append(fetch("arxiv_cs_ro_rss", "https://rss.arxiv.org/rss/cs.RO", parse_arxiv_rss))
    results.append(fetch("lerobot_github_releases", "https://api.github.com/repos/huggingface/lerobot/releases?per_page=1", parse_github))
    results.append(fetch("openvla_huggingface_metadata", "https://huggingface.co/api/models/openvla/openvla-7b", parse_huggingface))
    output = {
        "scope": "Unauthenticated, read-only reachability and metadata parsing; no production ingestion, weights, model calls, or cloud resources.",
        "client_task_date": "2026-11-08", "started_at_utc": started, "finished_at_utc": now(),
        "request_count": len(results), "retries": 0,
        "arxiv_minimum_gap_seconds": 3.1,
        "conclusion_limit": "A parseable HTTP response is not successful database ingestion or proof of future scheduler reliability.",
        "results": results,
    }
    DEST.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(json.dumps({key: result.get(key) for key in ("name", "http_status", "parse_ok", "error")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
