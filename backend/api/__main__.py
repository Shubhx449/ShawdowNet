"""
Entry point:  python3 -m backend.api [port] [--host H] [--data DIR] [--frontend DIR | --no-frontend]

Serves the REST API, the Mode 1 WebSocket (/ws/live) and -- if frontend/dist exists -- the web UI, all on
one port. Stored scenarios and experiments live in <project root>/data unless --data says otherwise
(anchored to the project, not the current directory, so it doesn't matter where you launch from).

There is NO authentication: it binds to 127.0.0.1 by default. Only pass --host 0.0.0.0 on a network you trust.
"""

import argparse
import os
import sys

from backend.api.server import run_server
from backend.api.store import Store

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m backend.api", description=__doc__.split("\n\n")[1])
    ap.add_argument("port", nargs="?", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data", default=os.path.join(ROOT, "data"), help="where scenarios/experiments are stored")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--frontend", default=os.path.join(ROOT, "frontend", "dist"), help="built frontend to serve")
    grp.add_argument("--no-frontend", action="store_true", help="API + WebSocket only")
    args = ap.parse_args(argv)

    static_dir = None
    if not args.no_frontend:
        if os.path.isfile(os.path.join(args.frontend, "index.html")):
            static_dir = args.frontend
        else:
            print(f"Note: no built frontend at {args.frontend}. Build it with:  cd frontend && npm ci && npm run build",
                  file=sys.stderr)
    try:
        run_server(Store(root_dir=args.data), host=args.host, port=args.port, static_dir=static_dir)
    except OSError as e:
        print(f"Cannot start on {args.host}:{args.port}: {e}. Is another server already using that port? "
              f"Try another, e.g.  python3 -m backend.api 8001", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
