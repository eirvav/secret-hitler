#!/usr/bin/env python3
"""Populate a running Secret Hitler lobby with bot players, all marked ready.

Each bot is a real seat in the server's game state (created via the same
/api/join + /api/ready calls the web page makes) — not a fake UI overlay.
By default it also opens one browser window per bot, pre-authenticated via
its own session token, so you can watch each bot's view of the game.

Usage:
  ./spawn_bots.py                       # 6 bots, readied, one Chrome window each
  ./spawn_bots.py --count 4
  ./spawn_bots.py --host 192.168.1.5 --port 8000
  ./spawn_bots.py --domain              # targets secret-hitler-role-generator.onrender.com
  ./spawn_bots.py --domain my-app.onrender.com
  ./spawn_bots.py --no-open             # just create + ready the bots
  ./spawn_bots.py --browser Safari
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request


def post(base_url, path, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        base_url + path, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())
    except urllib.error.URLError as e:
        print(f"Could not reach {base_url} — is server.py running? ({e.reason})")
        sys.exit(1)


def open_window(url, browser):
    """Best-effort: force a genuinely new window (not just a new tab)."""
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["open", "-na", browser, "--args", "--new-window", url],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    import webbrowser
    webbrowser.open_new(url)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--domain", nargs="?", const="secret-hitler-role-generator.onrender.com",
                     default=None,
                     help="bare domain to hit over HTTPS, no scheme needed "
                     "(default when flag is given with no value: "
                     "secret-hitler-role-generator.onrender.com); overrides --host/--port")
    ap.add_argument("--count", type=int, default=6, help="number of bots (default 6)")
    ap.add_argument("--prefix", default="Bot", help="name prefix (default 'Bot')")
    ap.add_argument("--browser", default="Google Chrome",
                     help="macOS app name used to force separate windows (default: Google Chrome)")
    ap.add_argument("--no-open", action="store_true",
                     help="create and ready the bots but don't open any browser windows")
    ap.add_argument("--delay", type=float, default=0.4,
                     help="seconds to wait between opening windows (default 0.4)")
    args = ap.parse_args()

    if args.domain:
        base_url = f"https://{args.domain.rstrip('/')}"
    else:
        base_url = f"http://{args.host}:{args.port}"
    print(f"Populating {base_url} with {args.count} ready bots...\n")

    # Join every bot before readying any of them: the server deals roles the
    # instant all *current* players are ready, and a room of 5 is itself a
    # valid size — readying one at a time would deal at 5 before bot 6 could
    # even take a seat.
    tokens = {}
    for i in range(1, args.count + 1):
        name = f"{args.prefix} {i}"
        joined = post(base_url, "/api/join", {"name": name})
        if "error" in joined:
            print(f"  [{name}] join failed: {joined['error']}")
            continue
        tokens[i] = joined["token"]
        print(f"  [{name}] joined.")

    opened = 0
    for i, token in tokens.items():
        name = f"{args.prefix} {i}"
        ready = post(base_url, "/api/ready", {"token": token, "ready": True})
        if "error" in ready:
            print(f"  [{name}] ready failed: {ready['error']}")
            continue
        print(f"  [{name}] marked ready.")

        if not args.no_open:
            url = f"{base_url}/?slot={i}&token={token}"
            open_window(url, args.browser)
            opened += 1
            time.sleep(args.delay)

    print()
    if not args.no_open:
        print(f"Opened {opened} browser window(s), one per bot, already joined and readied.")
    print("Done.")


if __name__ == "__main__":
    main()
