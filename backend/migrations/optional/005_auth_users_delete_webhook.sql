-- OPTIONAL, NOT RUN AUTOMATICALLY. Needs approval before it is applied to production.
--
-- Calls the backend whenever a row is deleted from auth.users (dashboard, API or the
-- backend itself) so the matching Wix member and app data are deleted too.
-- This is what the Supabase dashboard's "Database Webhooks" feature creates.
--
-- Before running, replace:
--   <BACKEND_URL>       e.g. https://livetranslatedubtool-production.up.railway.app
--   <WEBHOOK_SECRET>    the same value as the backend env var ACCOUNT_SYNC_WEBHOOK_SECRET
-- Do not commit a filled-in copy of this file.

DROP TRIGGER IF EXISTS lt_auth_user_deleted_webhook ON auth.users;

CREATE TRIGGER lt_auth_user_deleted_webhook
  AFTER DELETE ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION supabase_functions.http_request(
    '<BACKEND_URL>/api/v1/account/sync/supabase-user-deleted',
    'POST',
    '{"Content-Type":"application/json","Authorization":"Bearer <WEBHOOK_SECRET>"}',
    '{}',
    '5000'
  );

-- Roll back:
--   DROP TRIGGER IF EXISTS lt_auth_user_deleted_webhook ON auth.users;
