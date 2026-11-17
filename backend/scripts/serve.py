"""Run the SAYZO API and chat UI.

Usage (from the backend folder):
    python scripts/serve.py                # Supabase sign-in (needs SUPABASE_URL and key)
    python scripts/serve.py --dev-auth     # local demo: pick a person, no password

Then open http://127.0.0.1:8000. The first start loads the embedding model (a few seconds).
--dev-auth trusts whoever is picked in the browser. Use it on your own machine only; it is
refused unless the server listens on localhost.
"""

import argparse
import sys

import uvicorn

from app.api.container import build_container
from app.api.main import create_app
from app.config import get_settings

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> int:
    args = parse_args()
    settings = get_settings()
    if args.dev_auth:
        if args.host not in LOCAL_HOSTS:
            print("--dev-auth is only allowed on localhost.", file=sys.stderr)
            return 1
        settings = settings.model_copy(update={"auth_mode": "dev"})
    elif settings.auth_mode == "dev" and args.host not in LOCAL_HOSTS:
        print("AUTH_MODE=dev is only allowed on localhost.", file=sys.stderr)
        return 1
    app = create_app(lambda: build_container(settings))
    print(f"SAYZO on http://{args.host}:{args.port}  (auth: {settings.auth_mode})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--dev-auth", action="store_true", help="pick a person, no password")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
