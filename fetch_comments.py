#!/usr/bin/env python3
"""Reddit post -> all comments -> one big txt. curl_cffi chrome impersonation."""
import json, re, sys, time, urllib.parse
from curl_cffi import requests as cr

UA_OUT = "reddit-comments/1.0 (personal archive)"
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

# 1) resolve shortlink
r = S.get(SRC)
final = r.url
print("resolved:", final)
m = re.search(r"(https://www\.reddit\.com/r/[^?#]+/comments/[A-Za-z0-9]+[^?#]*)", final)
if not m:
    m2 = re.search(r"/comments/([A-Za-z0-9]+)", final)
    if not m2:
        print("FATAL cannot resolve post id"); sys.exit(2)
    base = "https://www.reddit.com/comments/%s" % m2.group(1)
else:
    base = m.group(1)
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
