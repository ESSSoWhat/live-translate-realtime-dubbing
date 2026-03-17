# Backend migrations

Run these in order against your Postgres database (Supabase SQL Editor or `psql`).

| File | Purpose |
|------|---------|
| `001_wix_api_key.sql` | Make `users.supabase_uid` nullable; add `users.api_key` for Wix API-key auth |

**Supabase:** Dashboard → SQL Editor → New query → paste file contents → Run.

**psql:** `psql $DATABASE_URL -f migrations/001_wix_api_key.sql`
