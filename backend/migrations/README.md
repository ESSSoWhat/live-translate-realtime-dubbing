# Backend migrations

Run these in order against your Postgres database (Supabase SQL Editor or `psql`).

| File | Purpose |
|------|---------|
| `001_wix_api_key.sql` | Make `users.supabase_uid` nullable; add `users.api_key` for Wix API-key auth |
| `002_verified_phone.sql` | Add `users.phone_e164` (unique) and `users.phone_verified_at` for translated calls |

**Supabase:** Dashboard → SQL Editor → New query → paste file contents → Run.

**psql:** `psql $DATABASE_URL -f migrations/001_wix_api_key.sql`
