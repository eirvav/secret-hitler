#!/usr/bin/env python3
"""Secret Hitler role dealer — zero-dependency LAN server.

Run:  python3 server.py
Then everyone on the same Wi-Fi opens the printed http://<ip>:8000 URL.
"""
import json
import os
import random
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", 8000))
STATIC_DIR = Path(__file__).parent
LOBBY_TIMEOUT = 90  # seconds without polling before a player is dropped (lobby only)

# players needed -> (liberals, regular fascists); Hitler is always +1
COMPOSITION = {
    5: (3, 1), 6: (4, 1), 7: (4, 2), 8: (5, 2), 9: (6, 2), 10: (6, 3),
}

LOCK = threading.Lock()
STATE = {
    "phase": "lobby",     # "lobby" | "dealt"
    "round": 0,
    "players": {},        # token -> {id, name, ready, last_seen, role, ...}
    "next_id": 1,
}


def now():
    return time.monotonic()


def prune_locked():
    """Drop players who stopped polling (closed tab) while in the lobby."""
    if STATE["phase"] != "lobby":
        return
    cutoff = now() - LOBBY_TIMEOUT
    stale = [t for t, p in STATE["players"].items() if p["last_seen"] < cutoff]
    for t in stale:
        del STATE["players"][t]


def unstick_locked():
    """An emptied room must never stay stuck in the dealt phase."""
    if not STATE["players"]:
        STATE["phase"] = "lobby"


def maybe_deal_locked():
    players = list(STATE["players"].values())
    n = len(players)
    if STATE["phase"] != "lobby" or n not in COMPOSITION:
        return
    if not all(p["ready"] for p in players):
        return

    _liberals, n_fascists = COMPOSITION[n]
    shuffled = random.sample(players, n)
    hitler = shuffled[0]
    fascists = shuffled[1:1 + n_fascists]

    for p in players:
        p["role"] = "liberal"
        p["knows"] = {}
    hitler["role"] = "hitler"
    for f in fascists:
        f["role"] = "fascist"
    for f in fascists:
        f["knows"] = {
            "fellow_fascists": [x["name"] for x in fascists if x is not f],
            "hitler": hitler["name"],
        }
    STATE["phase"] = "dealt"
    STATE["round"] += 1
    # unique per deal, so clients' "role already revealed" flags can never
    # collide with a round number from before a server restart
    STATE["deal_id"] = secrets.token_hex(4)


def reset_locked():
    STATE["phase"] = "lobby"
    for p in STATE["players"].values():
        p["ready"] = False
        p["role"] = None
        p["knows"] = {}
        p["last_seen"] = now()


def public_state_locked(me):
    players = sorted(STATE["players"].values(), key=lambda p: p["id"])
    out = {
        "phase": STATE["phase"],
        "round": STATE["round"],
        "players": [{"id": p["id"], "name": p["name"], "ready": p["ready"]} for p in players],
        "count": len(players),
        "ready_count": sum(1 for p in players if p["ready"]),
        "valid_count": len(players) in COMPOSITION,
        "you": {"id": me["id"], "name": me["name"], "ready": me["ready"]},
    }
    if STATE["phase"] == "dealt" and me.get("role"):
        out["deal_id"] = STATE.get("deal_id")
        out["you"]["role"] = me["role"]
        out["you"]["knows"] = me.get("knows", {})
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 4096:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def get_me(self, token):
        return STATE["players"].get(token or "")

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/" or path == "/index.html":
            html = (STATIC_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html)
            return
        if path.startswith("/svg/"):
            name = urllib.parse.unquote(path[len("/svg/"):])
            file = STATIC_DIR / "svg" / name
            if ("/" in name or ".." in name or not name.endswith(".svg")
                    or not file.is_file()):
                self.send_json({"error": "not_found"}, 404)
                return
            body = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/state":
            params = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
            with LOCK:
                me = self.get_me(params.get("token"))
                if not me:
                    self.send_json({"error": "unknown_token"}, 401)
                    return
                me["last_seen"] = now()
                prune_locked()
                maybe_deal_locked()
                self.send_json(public_state_locked(me))
            return
        self.send_json({"error": "not_found"}, 404)

    def do_POST(self):
        body = self.read_body()
        if self.path == "/api/join":
            name = str(body.get("name", "")).strip()[:16]
            if not name:
                self.send_json({"error": "Name required."}, 400)
                return
            with LOCK:
                prune_locked()
                existing = next(
                    ((t, p) for t, p in STATE["players"].items()
                     if p["name"].lower() == name.lower()), None)
                if existing:
                    # reclaim the seat unconditionally (trust game between
                    # friends): the newest device wins, the old one drops out
                    old_token, player = existing
                    del STATE["players"][old_token]
                    token = secrets.token_urlsafe(16)
                    player["last_seen"] = now()
                    STATE["players"][token] = player
                    self.send_json({"token": token, "reclaimed": True})
                    return
                if STATE["phase"] != "lobby":
                    self.send_json({"error": "A round is in progress. To rejoin, use your exact name."}, 409)
                    return
                if len(STATE["players"]) >= 10:
                    self.send_json({"error": "The room is full (10 players max)."}, 409)
                    return
                token = secrets.token_urlsafe(16)
                STATE["players"][token] = {
                    "id": STATE["next_id"], "name": name, "ready": False,
                    "last_seen": now(), "role": None, "knows": {},
                }
                STATE["next_id"] += 1
                self.send_json({"token": token})
            return

        token = body.get("token")
        with LOCK:
            me = self.get_me(token)
            if not me:
                self.send_json({"error": "unknown_token"}, 401)
                return
            me["last_seen"] = now()

            if self.path == "/api/ready":
                if STATE["phase"] == "lobby":
                    me["ready"] = bool(body.get("ready"))
                    prune_locked()
                    maybe_deal_locked()
                self.send_json(public_state_locked(me))
            elif self.path == "/api/endgame":
                # any single player may cancel the round for everyone
                if STATE["phase"] == "dealt":
                    reset_locked()
                self.send_json(public_state_locked(me))
            elif self.path == "/api/leave":
                del STATE["players"][token]
                unstick_locked()
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "not_found"}, 404)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("\n  SECRET HITLER — role dealer")
    print(f"  Players join at:  http://{lan_ip()}:{PORT}\n")
    print("  Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
