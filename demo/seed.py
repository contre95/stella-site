#!/usr/bin/env python3
"""Seed a running Stella instance with the decks demo/gen wrote.

Each deck export is uploaded through the web UI's import endpoint, which keeps
its scheduling state and review history. Groups and tags are then set through
the MCP endpoint (unauthenticated in single-user mode), whose tools return
JSON with the deck and group ids.

Usage: seed.py <base_url> <decks_dir>
"""

import json
import os
import sys
import urllib.request
import uuid


def import_deck(base, path):
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'
        "Content-Type: application/json\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        base + "/import", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "HX-Request": "true"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


class MCP:
    """A minimal StreamableHTTP MCP client: initialize, then tools/call."""

    def __init__(self, url):
        self.url, self.session, self.next_id = url, None, 1
        self.rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "stella-demo-seed", "version": "1"},
        })
        self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def post(self, msg):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.session = r.headers.get("Mcp-Session-Id") or self.session
            body = r.read().decode()
            if r.headers.get_content_type() == "text/event-stream":
                # The response is a single SSE event; its data lines hold the JSON.
                body = "\n".join(l[5:].strip() for l in body.splitlines() if l.startswith("data:"))
            return json.loads(body) if body.strip() else None

    def rpc(self, method, params):
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        self.next_id += 1
        resp = self.post(msg) or {}
        if "error" in resp:
            raise RuntimeError(f"{method}: {resp['error']}")
        return resp["result"]

    def call(self, tool, **args):
        res = self.rpc("tools/call", {"name": tool, "arguments": args})
        if res.get("isError"):
            raise RuntimeError(f"{tool}: {res['content']}")
        return res.get("structuredContent")


def main():
    base, decks_dir = sys.argv[1].rstrip("/"), sys.argv[2]
    with open(os.path.join(decks_dir, "manifest.json")) as f:
        manifest = json.load(f)

    for d in manifest["decks"]:
        import_deck(base, os.path.join(decks_dir, d["file"]))
        print(f"  imported {d['name']}")

    mcp = MCP(base + "/mcp")
    deck_ids = {d["name"]: d["deckId"] for d in mcp.call("list_decks")["decks"]}
    group_ids = {g: mcp.call("create_group", name=g)["groupId"] for g in manifest["groups"]}
    for d in manifest["decks"]:
        deck_id = deck_ids[d["name"]]
        if d.get("group"):
            mcp.call("set_deck_group", deckId=deck_id, groupId=group_ids[d["group"]])
        for tag in d.get("tags", []):
            mcp.call("tag_deck", deckId=deck_id, tag=tag)
    print(f"  {len(group_ids)} groups, {sum(len(d.get('tags', [])) for d in manifest['decks'])} tags")


if __name__ == "__main__":
    main()
