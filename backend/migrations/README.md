# Backend migrations

Run these in order against your Postgres database (Supabase SQL Editor or `psql`).

| File | Purpose |
|------|---------|
| `001_wix_api_key.sql` | Make `users.supabase_uid` nullable; add `users.api_key` for Wix API-key auth |
| `002_verified_phone.sql` | Add `users.phone_e164` (unique) and `users.phone_verified_at` for translated calls |
| `003_phone_verification_codes.sql` | Shared SMS verification state (hashed codes, attempts, send limits) |
| `004_account_deletion_sync.sql` | `users.wix_member_id` link + `account_deletion_audit` table for two-way Wix/app deletion sync |
| `optional/005_auth_users_delete_webhook.sql` | **Not run automatically.** Database webhook on `auth.users` DELETE that calls the backend (needs `ACCOUNT_SYNC_WEBHOOK_SECRET`) |

**Supabase:** Dashboard → SQL Editor → New query → paste file contents → Run.

**psql:** `psql $DATABASE_URL -f migrations/001_wix_api_key.sql`
