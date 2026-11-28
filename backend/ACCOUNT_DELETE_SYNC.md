# Two-way account deletion sync (Wix member <-> app account)

Keeps the existing link between Wix members and LiveTranslate app accounts, and makes a
deletion on either side delete the other. Everything here is **off** until
`ACCOUNT_DELETE_SYNC_ENABLED=true`.

## How it works

| Trigger | Path | Effect |
|---|---|---|
| Wix member deleted (dashboard, API) | Velo `backend/events.js` `wixMembers_onMemberDeleted` -> `POST /api/v1/account/sync/wix-member-deleted` (secret `LT_SYNC_SECRET`) | Deletes the Supabase auth user, `public.users` row (cascades usage, voices, phone verification) and desktop handoffs for the row whose `wix_member_id` equals the deleted member id |
| App account deleted by the user | `DELETE /api/v1/user/me` with `{"confirm_email": "<own email>"}` | Deletes the Wix member (Members REST API), the auth user and the app rows |
| Supabase auth user deleted (dashboard, API, or the backend) | DB trigger on `auth.users` -> `POST /api/v1/account/sync/supabase-user-deleted` (secret `ACCOUNT_SYNC_WEBHOOK_SECRET`) | Deletes the Wix member and the app rows |

Safety rules:

* **Exact matching only.** Wix events match on the stored `wix_member_id` (a Wix GUID).
  Supabase deletions match `supabase_uid`, then an email that is *equal* (case-insensitive) to the
  deleted user's; a Wix member is found by exact `loginEmail`. More than one Wix match deletes
  nothing (HTTP 409). No `LIKE`, no partial match.
* **Idempotent / loop-safe.** Missing Wix member, auth user or row = already deleted. The echo
  event each deletion causes finds nothing left. An in-process guard also stops the echo racing the
  first pass.
* **Order is safe to retry.** External system first, local rows last; on a failure (HTTP 502) the
  `users` row keeps the link so a retry can finish.
* **Audit log.** Every attempt writes `account_deletion_audit` (origin, outcome, steps, ids; the email
  only as a SHA-256 hash).
* **Protected emails** (`ACCOUNT_SYNC_PROTECTED_EMAILS`) are never deleted by sync.

Not covered: ElevenLabs cloned voices, Stripe/PayPal subscriptions (the user row, and with it the
link to them, is deleted; cancel paid plans first).

## Production steps (need approval)

1. **Database**: run `migrations/004_account_deletion_sync.sql` (adds `users.wix_member_id`,
   `account_deletion_audit`).
2. **Railway env vars** (production service):
   * `ACCOUNT_SYNC_WEBHOOK_SECRET` = new random string (>= 32 chars)
   * `ACCOUNT_SYNC_PROTECTED_EMAILS` = comma-separated owner/support/test emails
   * `WIX_API_KEY`, `WIX_ACCOUNT_ID`, optional `WIX_SITE_ID` (already used for plan sync). The API key
     must also have the permission **Manage Members** (`SCOPE.DC-MEMBERS.MANAGE-MEMBERS`;
     method permission `MEMBERS.MEMBER_DELETE`) and **Read Members** (member lookup).
   * `ACCOUNT_DELETE_SYNC_ENABLED=true` (last, after the steps below)
3. **Merge and deploy** this PR (merging auto-deploys production Railway).
4. **Backfill links** for existing users (dry run first, prints counts only):
   `python -m scripts.backfill_wix_member_ids` then `--apply`. New and returning members are also
   linked automatically when they open the website's API key page.
5. **Wix (publish the site)**: add/merge `wix-app/velo-pages/events.js` into `backend/events.js`;
   replace `backend/api-key.web.js` with the updated copy; confirm Secrets Manager has `LT_SYNC_SECRET`.
6. **Supabase webhook**: fill the placeholders in `migrations/optional/005_auth_users_delete_webhook.sql`
   and run it (or create the equivalent Database Webhook: table `auth.users`, event DELETE, HTTP POST,
   header `Authorization: Bearer <ACCOUNT_SYNC_WEBHOOK_SECRET>`).
7. Test with a throwaway member and a throwaway app account in each direction; check
   `account_deletion_audit`.
