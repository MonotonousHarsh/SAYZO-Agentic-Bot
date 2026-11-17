"""Create Supabase Auth accounts for the people in the dataset directory, so they can sign in.

Usage (from the backend folder):
    python scripts/create_demo_users.py --password "choose-a-demo-password"

Needs SUPABASE_URL and SUPABASE_SECRET_KEY (Supabase dashboard -> Project Settings -> API
Keys -> secret key) in .env. The secret key stays on this machine: it is never sent to the
browser. Accounts are created already confirmed. Re-running skips existing accounts. On first
sign-in, each account is linked to the directory profile with the same email.
"""

import argparse
import sys
from pathlib import Path

import httpx

from app.config import get_settings
from app.modules.synthetic_data import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "datasets" / "seed"


def main() -> int:
    args = parse_args()
    settings = get_settings()
    if not settings.supabase_url or settings.supabase_secret_key is None:
        print("Set SUPABASE_URL and SUPABASE_SECRET_KEY in .env first.", file=sys.stderr)
        return 1
    if len(args.password) < 8:
        print("Use a password of at least 8 characters.", file=sys.stderr)
        return 1
    secret = settings.supabase_secret_key.get_secret_value()
    headers = {"apikey": secret}
    if not secret.startswith("sb_secret_"):  # a legacy service_role JWT also goes here
        headers["Authorization"] = f"Bearer {secret}"

    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users"
    directory = load_dataset(DATASET).directory
    failures = 0
    with httpx.Client(timeout=20.0) as client:
        for organisation, person in directory.people():
            response = client.post(
                url,
                headers=headers,
                json={"email": person.email, "password": args.password, "email_confirm": True},
            )
            if response.status_code in (200, 201):
                outcome = "created"
            elif response.status_code == 422 and "already" in response.text.lower():
                outcome = "already exists"
            else:
                outcome = f"FAILED ({response.status_code}: {response.text[:120]})"
                failures += 1
            print(f"{person.email:<32} {person.role.value:<7} {organisation.name:<14} {outcome}")
    return 1 if failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--password", required=True, help="the demo password for every account")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
