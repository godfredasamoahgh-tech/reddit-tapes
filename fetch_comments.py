#!/usr/bin/env python3
"""Reddit post -> all comments -> one big txt. curl_cffi chrome impersonation."""
import json, re, sys, time, urllib.parse
from curl_cffi import requests as cr

UA_OUT = "reddit-comments/1.0 (personal archive)"
# 0) direct reddit .json attempt (full tree, real id) — bypass share-link entirely
DIRECT_JSON = "https://www.reddit.com/comments/1wu3hdu.json?raw_json=1&limit=500&depth=10"
try:
    r0 = S.get(DIRECT_JSON)
    print("direct json status:", r0.status_code, "len:", len(r0.text or ""))
    if r0.status_code == 200 and (r0.text or "").lstrip().startswith("["):
        open("./out/reddit_raw.json", "w").write(r0.text)
        print("DIRECT_JSON_OK")
except Exception as e:
    print("direct json ERR", str(e)[:110])

SRC = "https://www.reddit.com/r/confusing_perspective/s/pE3kcxlldl"
OUT = "./out/confusing_perspective_comments.txt"

S = cr.Session(impersonate="chrome", timeout=40)

def jget(url, tries=3):
    for i in range(tries):
        try:
            r = S.get(url, headers={"User-Agent": UA_OUT})
            if r.status_code == 200:
                return r.json()
            sys.stderr.write("HTTP %d %s\n" % (r.status_code, url[:110]))
        except Exception as e:
            sys.stderr.write("err %s\n" % str(e)[:120])
        time.sleep(1.5 * (i + 1))
    return None

# 1) resolve shortlink (multi-probe)
def probe(url, tag):
    try:
        r = S.get(url)
        txt = r.text or ""
        print("probe %s -> %d %s len=%d" % (tag, r.status_code, r.url, len(txt)))
        ids = list(dict.fromkeys(re.findall(r"/comments/([A-Za-z0-9]{5,8})", txt)))
        cans = re.findall(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', txt)
        pms = list(dict.fromkeys(re.findall(r'"permalink"\s*:\s*"([^"]+)"', txt)))[:3]
        print("  ids=%s canonical=%s permalinks=%s" % (ids[:6], cans[:2], pms[:2]))
        m = re.search(r"/comments/([A-Za-z0-9]{5,8})", r.url)
        if m:
            return "https://www.reddit.com/comments/" + m.group(1)
        if ids:
            return "https://www.reddit.com/comments/" + ids[0]
        for c in cans:
            mc = re.search(r"/comments/([A-Za-z0-9]{5,8})", c)
            if mc: return "https://www.reddit.com/comments/" + mc.group(1)
        for p in pms:
            mp = re.search(r"/comments/([A-Za-z0-9]{5,8})", p)
            if mp: return "https://www.reddit.com" + p.split("?")[0]
        if txt.lstrip().startswith("{"):
            try:
                jj = json.loads(txt)
                blob = json.dumps(jj)
                mb = re.search(r"/comments/([A-Za-z0-9]{5,8})", blob)
                if mb: return "https://www.reddit.com/comments/" + mb.group(1)
            except Exception: pass
    except Exception as e:
        print("probe %s ERR %s" % (tag, str(e)[:110]))
    return None

resolved = None
for tag, u in [
    ("share", SRC),
    ("share.json", SRC + ".json"),
    ("root-s", "https://www.reddit.com/s/pE3kcxlldl"),
    ("old-share", SRC.replace("www.reddit.com", "old.reddit.com")),
    ("np-share", SRC.replace("www.reddit.com", "np.reddit.com")),
]:
    resolved = probe(u, tag)
    if resolved:
        break
    time.sleep(1)

if not resolved:
    # wayback from runner
    try:
        wb = json.loads(S.get("https://archive.org/wayback/available?url=" +
            urllib.parse.quote(SRC.replace("https://", ""), safe="")).text)
        snaps = wb.get("archived_snapshots", {})
        print("wayback:", json.dumps(snaps)[:250])
        cand = (snaps.get("closest") or {}).get("url", "")
        m = re.search(r"/comments/([A-Za-z0-9]{5,8})", cand)
        if m: resolved = "https://www.reddit.com/comments/" + m.group(1)
    except Exception as e:
        print("wayback ERR", str(e)[:110])

if not resolved:
    print("FATAL cannot resolve post id"); sys.exit(2)

final = resolved
print("resolved ->", final)
m = re.search(r"(https://www\.reddit\.com/(?:r/[^?#]+/)?comments/[A-Za-z0-9]+)", final)
base = m.group(1) if m else final
post_id = re.search(r"/comments/([A-Za-z0-9]+)", base).group(1)
print("post:", post_id)

# 2) post + comments json
data = jget(base + ".json?raw_json=1&limit=500&sort=top")
if not data:
    print("FATAL json fetch failed"); sys.exit(3)

post = data[0]["data"]["children"][0]["data"]
title = post.get("title", "")
selftext = post.get("selftext", "") or ""
score = post.get("score", 0)
ncomments_api = post.get("num_comments", 0)
sub = post.get("subreddit", "")
author = post.get("author", "")
permalink = "https://www.reddit.com" + post.get("permalink", "")

lines = []
lines.append("=" * 78)
lines.append("POST: %s" % title)
lines.append("r/%s | u/%s | score %s | api num_comments %s" % (sub, author, score, ncomments_api))
lines.append("URL: %s" % permalink)
lines.append("=" * 78)
if selftext:
    lines.append("[SELFTEXT]")
    lines.append(selftext)
    lines.append("-" * 78)

comments = []          # (depth, author, score, body)
more_ids = []

def walk(node, depth):
    t = node.get("kind")
    d = node.get("data", {})
    if t == "t1":
        body = d.get("body", "")
        if d.get("removed_by_category") or body in ("[deleted]", "[removed]"):
            body = d.get("body") or "[deleted]"
        comments.append((depth, d.get("author") or "[deleted]", d.get("score", 0), body))
        rep = d.get("replies")
        if isinstance(rep, dict):
            for ch in rep.get("data", {}).get("children", []):
                walk(ch, depth + 1)
        mc = d.get("more")
        if isinstance(mc, dict) and mc.get("count"):
            more_ids.extend(mc.get("children", []))
    elif t == "more":
        more_ids.extend(d.get("children", []))

for ch in data[1]["data"]["children"]:
    walk(ch, 0)

# 3) expand 'more' (best effort, 2 rounds)
for rnd in range(2):
    if not more_ids:
        break
    batch = more_ids[:100]
    more_ids = more_ids[100:]
    q = urllib.parse.urlencode({"api_type": "json", "link_id": "t3_" + post_id,
                                "children": ",".join(batch), "limit": "500",
                                "raw_json": "1", "sort": "top"})
    mj = jget("https://www.reddit.com/api/morechildren.json?" + q)
    if not mj:
        print("morechildren failed, ids left:", len(more_ids)); break
    things = (mj.get("json") or {}).get("data", {}).get("things", [])
    byid = {}
    for t in things:
        if t.get("kind") == "t1":
            byid[t["data"]["parent_id"]] = t  # not used directly; flat append below
    added = 0
    for t in things:
        if t.get("kind") != "t1":
            continue
        d = t["data"]
        comments.append((1, d.get("author") or "[deleted]", d.get("score", 0),
                         d.get("body", "")))
        added += 1
    print("morechildren round %d: +%d" % (rnd + 1, added))
    if not added:
        break

# 4) render
lines.append("")
lines.append("COMMENTS: %d collected (api count: %s)" % (len(comments), ncomments_api))
lines.append("=" * 78)
for depth, a, sc, body in comments:
    ind = "  " * depth
    prefix = "%s[u/%s] %s pts:" % (ind, a, sc)
    lines.append(prefix)
    for bl in (body or "").split("\n"):
        lines.append(ind + "  " + bl)
    lines.append("")

txt = "\n".join(lines)
import os
os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write(txt)
print("WROTE", OUT, "bytes", len(txt.encode()))
print("comments:", len(comments), "| api:", ncomments_api, "| rounds with more:", bool(more_ids))
