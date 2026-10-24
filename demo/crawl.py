#!/usr/bin/env python3
"""Crawl a running Stella instance into a static site.

Full pages (no HX-Request header) are saved as <path>/index.html. HTMX
fragments are saved as _hx/<fnv1a(canonical url)>/index.html, and demo/static-demo.js
rewrites HTMX requests to those files in the browser. Dynamic resources
(artwork, audio streams, exports) are saved under _res/ and every reference to
them is rewritten.

Copied from soulsolid-site's demo/crawl.py; only EXCLUDE is Stella-specific.

Usage: crawl.py <base_url> <out_dir> [--public public] [--strip <prefix> ...]
  --strip  text removed from every saved document (e.g. the temporary work
           dir, so no temporary paths leak into the demo)
"""

import argparse
import html
import json
import mimetypes
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from html.parser import HTMLParser

SHIM_PATH = "/static-demo.js"

# GET endpoints with side effects or nothing to show. They are left unresolved,
# so in the demo they 404 and the shim shows a "not available" toast.
EXCLUDE = [re.compile(p) for p in (
    r"^/mcp(/|$)",
    r"^/auth/",
    r"^/\.well-known/",
)]

# Max variants generated per form, so a form with many selects stays bounded.
MAX_VARIANTS = 40

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
INPUTS = {"input", "select", "textarea"}


# --- canonical URLs: must match canonicalURL() and fnv1a() in demo/static-demo.js ---

def js_encode(s):
    return urllib.parse.quote(s, safe="-_.!~*'()")


def canonical_url(pathname, pairs):
    kept = sorted((str(k), str(v)) for k, v in pairs if v != "" and k != "org.htmx.cache-buster")
    if not kept:
        return pathname
    return pathname + "?" + "&".join(f"{js_encode(k)}={js_encode(v)}" for k, v in kept)


def fnv1a(s):
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def normalize_path(path):
    """Percent-encode a path the way the browser's URL parser does."""
    return urllib.parse.quote(urllib.parse.unquote(path), safe="/:@!$&'()*+,;=-._~")


def split_url(raw):
    u = urllib.parse.urlsplit(raw)
    return normalize_path(u.path or "/"), urllib.parse.parse_qsl(u.query, keep_blank_values=True)


# --- a minimal DOM, just enough to emulate htmx's GET parameter collection ---

class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, dict(attrs), parent, [], ""

    def closest(self, tag):
        n = self
        while n is not None and n.tag != tag:
            n = n.parent
        return n

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def get(self, k, default=None):
        v = self.attrs.get(k, default)
        return default if v is None else v


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.text += data


def parse(doc):
    tb = TreeBuilder()
    tb.feed(doc)
    return tb.root


def input_value(el):
    if el.tag == "select":
        options = [o for o in el.walk() if o.tag == "option"]
        chosen = next((o for o in options if "selected" in o.attrs), options[0] if options else None)
        return None if chosen is None else chosen.get("value", chosen.text.strip())
    if el.tag == "textarea":
        return el.text
    return el.get("value", "on" if el.get("type") in ("checkbox", "radio") else "")


def includable(el):
    if el.tag not in INPUTS or not el.get("name") or "disabled" in el.attrs:
        return False
    if el.get("type") in ("button", "submit", "reset", "file", "image"):
        return False
    if el.get("type") in ("checkbox", "radio"):
        return "checked" in el.attrs
    return True


def variants_for(el):
    """Alternative values a user could pick for one input: select options, checkbox toggles."""
    if el.tag == "select" and el.get("name"):
        return [o.get("value", o.text.strip()) for o in el.walk() if o.tag == "option"]
    if el.get("type") == "checkbox" and el.get("name"):
        return [None, el.get("value", "on")]
    return []


def resolve_includes(el, root):
    spec = (el.get("hx-include") or "").strip()
    if not spec:
        return []
    found = []
    for part in spec.split(","):
        part = part.strip()
        if part == "this":
            found.append(el)
        elif part.startswith("closest "):
            n = el.closest(part[len("closest "):].strip())
            if n is not None:
                found.append(n)
        elif part.startswith("#"):
            found += [n for n in root.walk() if n.get("id") == part[1:]]
        else:
            print(f"  warn: unsupported hx-include {part!r}", file=sys.stderr)
    return found


def request_variants(el, root):
    """Every parameter list htmx could send for a GET from el, default values first."""
    inputs = []
    if el.tag in INPUTS:
        inputs.append(el)
    for inc in resolve_includes(el, root):
        for n in inc.walk():
            if n.tag in INPUTS and n not in inputs:
                inputs.append(n)

    def collect(override=None):
        pairs = []
        for n in inputs:
            if override and n is override[0]:
                if override[1] is not None:
                    pairs.append((n.get("name"), override[1]))
            elif includable(n):
                pairs.append((n.get("name"), input_value(n)))
        vals = el.get("hx-vals")
        if vals and not vals.strip().startswith(("js:", "javascript:")):
            try:
                pairs += [(k, v if isinstance(v, str) else json.dumps(v)) for k, v in json.loads(vals).items()]
            except ValueError:
                pass
        return pairs

    out = [collect()]
    for n in inputs:
        for v in variants_for(n):
            out.append(collect((n, v)))
            if len(out) >= MAX_VARIANTS:
                return out
    return out


# --- crawling ---

class Crawler:
    def __init__(self, base, public_dir):
        self.base = base.rstrip("/")
        self.public_dir = public_dir
        self.pages = {}       # path -> html (full pages)
        self.fragments = {}   # canonical url -> html
        self.resources = {}   # url as referenced -> (published path, bytes)
        self.queue = deque()
        self.seen = set()
        self.failed = []

    def is_static(self, path):
        f = os.path.join(self.public_dir, urllib.parse.unquote(path).lstrip("/"))
        return os.path.isfile(f)

    def excluded(self, path):
        return any(p.search(path) for p in EXCLUDE)

    def fetch(self, url, htmx):
        req = urllib.request.Request(self.base + url)
        if htmx:
            req.add_header("HX-Request", "true")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.headers.get_content_type(), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get_content_type(), e.read()

    def enqueue(self, kind, url):
        key = (kind, url)
        if key not in self.seen:
            self.seen.add(key)
            self.queue.append(key)

    def discover(self, doc):
        root = parse(doc)
        for el in root.walk():
            hx = el.get("hx-get")
            if hx and hx.startswith("/"):
                path, query = split_url(html.unescape(hx))
                if self.excluded(path):
                    continue
                for pairs in request_variants(el, root):
                    self.enqueue("fragment", canonical_url(path, query + pairs))
                if el.get("hx-push-url") == "true":
                    self.enqueue("page", path)
            for attr in ("href", "src"):
                ref = el.get(attr)
                if not ref or not ref.startswith("/") or ref.startswith("//"):
                    continue
                path, _ = split_url(ref)
                if self.is_static(path) or self.excluded(path):
                    continue
                if attr == "href" and el.tag == "a" and "download" not in el.attrs:
                    self.enqueue("page", ref)
                else:
                    self.enqueue("resource", ref)

    def save_resource(self, ref, ctype, body):
        ext = mimetypes.guess_extension(ctype or "") or ""
        if ext in (".jpe", ".jpeg"):
            ext = ".jpg"
        path, query = split_url(ref)
        self.resources[ref] = (f"/_res/{fnv1a(canonical_url(path, query))}{ext}", ctype, body)

    def run(self, start="/"):
        self.enqueue("page", start)
        while self.queue:
            kind, url = self.queue.popleft()
            status, ctype, body = self.fetch(url, htmx=(kind == "fragment"))
            if status >= 400:
                self.failed.append((status, kind, url))
                continue
            # Fragments are saved whatever their type: counters come back as text/plain.
            if kind == "resource" or (kind == "page" and ctype != "text/html"):
                self.save_resource(url, ctype, body)
                continue
            doc = body.decode("utf-8", "replace")
            if kind == "page":
                # Pages are saved by path, so links like /review?learnahead=1 share
                # /review's file: the query-less URL wins, else the first one seen.
                path, query = split_url(url)
                if not query or path not in self.pages:
                    self.pages[path] = doc
            else:
                self.fragments[url] = doc
            if ctype == "text/html":
                self.discover(doc)
            total = len(self.pages) + len(self.fragments) + len(self.resources)
            if total % 100 == 0:
                print(f"  crawled {total} documents, {len(self.queue)} queued")


# --- output ---

ATTR_RE = re.compile(r'\b(src|href)="(/[^"]*)"')
EVERY_RE = re.compile(r"(,\s*)?every \d+m?s")


def strip_all(s, strip):
    for prefix in strip:
        s = s.replace(prefix, "").replace(js_encode(prefix), "")
    return s


def rewrite(doc, resources, strip):
    def sub(m):
        res = resources.get(html.unescape(m.group(2)))
        return f'{m.group(1)}="{res[0]}"' if res else m.group(0)

    doc = ATTR_RE.sub(sub, doc)
    # Nothing ever changes in the demo, so polling only burns requests.
    doc = re.sub(r'hx-trigger="([^"]*)"', lambda m: 'hx-trigger="%s"' % (EVERY_RE.sub("", m.group(1)).strip(" ,") or "load"), doc)
    return strip_all(doc, strip)


def inject_shim(doc):
    tag = f'<script src="{SHIM_PATH}"></script>'
    # Not every static host sends a charset header; without one the UTF-8 icons turn to mojibake.
    if "charset" not in doc[:1024].lower():
        tag = '<meta charset="utf-8">\n  ' + tag
    if "</head>" in doc:
        return doc.replace("</head>", f"  {tag}\n</head>", 1)
    return tag + doc


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data.encode("utf-8") if isinstance(data, str) else data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("out")
    ap.add_argument("--public", default="public")
    ap.add_argument("--strip", action="append", default=[])
    args = ap.parse_args()

    c = Crawler(args.base, args.public)
    print(f"crawling {args.base}")
    c.run()

    strip = [args.base] + sorted(args.strip, key=len, reverse=True)
    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    shutil.copytree(args.public, args.out)
    shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static-demo.js"), args.out + SHIM_PATH)

    for path, doc in c.pages.items():
        doc = inject_shim(rewrite(doc, c.resources, strip))
        write(os.path.join(args.out, urllib.parse.unquote(path).strip("/"), "index.html"), doc)
    for url, doc in c.fragments.items():
        # Hash the stripped URL: that is the one the browser will see and compute.
        # A directory index, because hosts like Cloudflare Pages redirect "x.html" to "x".
        write(os.path.join(args.out, "_hx", fnv1a(strip_all(url, strip)), "index.html"), rewrite(doc, c.resources, strip))
    for res_path, ctype, body in c.resources.values():
        # Text resources could carry the work dir too.
        if ctype.startswith("text/") or ctype.endswith("json"):
            body = strip_all(body.decode("utf-8", "replace"), strip)
        write(args.out + res_path, body)
    # Unknown deep links land on the dashboard instead of a bare 404.
    write(os.path.join(args.out, "404.html"), '<!doctype html><meta http-equiv="refresh" content="0; url=/">')

    print(f"saved {len(c.pages)} pages, {len(c.fragments)} fragments, {len(c.resources)} resources to {args.out}")
    for status, kind, url in c.failed:
        print(f"  {status} {kind} {url}", file=sys.stderr)


if __name__ == "__main__":
    main()
