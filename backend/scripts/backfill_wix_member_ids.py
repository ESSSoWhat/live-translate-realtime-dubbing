"""Backfill users.wix_member_id for existing app accounts (dry run by default).

Matches ONLY when exactly one Wix member's login email equals the app account's email.
Run from backend/ with the production env loaded (never print or commit it):

    python -m scripts.backfill_wix_member_ids            # dry run: prints counts only
    python -m scripts.backfill_wix_member_ids --apply    # writes wix_member_id

Requires migration 004 and WIX_API_KEY / WIX_ACCOUNT_ID (WIX_SITE_ID optional).
"""

from __future__ import annotations

import argparse
import asyncio

from app.services import account_sync
from app.services.supabase_client import create_auth_client


async def run(apply: bool) -> dict[str, int]:
    sb = await create_auth_client()
    result = await sb.table("users").select("id,email,wix_member_id").is_("wix_member_id", "null").execute()
    counts = {"checked": 0, "matched": 0, "no_wix_member": 0, "ambiguous": 0, "linked": 0, "errors": 0}
    for row in result.data or []:
        counts["checked"] += 1
        email = row.get("email") or ""
        if not email or email.endswith("@users.local"):
            counts["no_wix_member"] += 1
            continue
        try:
            member_id = await account_sync.find_wix_member_id_by_email(email)
        except account_sync.AmbiguousMatchError:
            counts["ambiguous"] += 1
            continue
        except account_sync.AccountSyncError:
            counts["errors"] += 1
            continue
        if not member_id:
            counts["no_wix_member"] += 1
            continue
        counts["matched"] += 1
        if apply and await account_sync.link_wix_member_id(sb, row["id"], member_id):
            counts["linked"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the links (default: dry run)")
    args = parser.parse_args()
    print(asyncio.run(run(args.apply)))  # counts only; no emails or ids


if __name__ == "__main__":
    main()
