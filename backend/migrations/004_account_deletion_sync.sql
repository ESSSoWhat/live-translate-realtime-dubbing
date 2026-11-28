-- Migration: two-way account deletion sync (Wix member <-> app account)
-- Run once in Supabase SQL Editor (or psql) after 003_phone_verification_codes.sql.
-- Idempotent: safe to run multiple times.

-- 1. Link each app account to its Wix member (a Wix GUID). A Wix "member deleted"
--    event only carries the member id, so this link is what lets Wix deletions reach us.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS wix_member_id text;
CREATE UNIQUE INDEX IF NOT EXISTS users_wix_member_id_key
  ON public.users (wix_member_id) WHERE wix_member_id IS NOT NULL;

-- 2. Audit log of every deletion-sync attempt. The email is stored only as a SHA-256 hash.
CREATE TABLE IF NOT EXISTS public.account_deletion_audit (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  origin text NOT NULL CHECK (origin IN ('wix', 'supabase', 'app')),
  outcome text NOT NULL CHECK (outcome IN ('deleted', 'no_match', 'protected', 'error')),
  user_id uuid,            -- public.users.id (no FK: the row is deleted)
  supabase_uid text,
  wix_member_id text,
  email_sha256 text,
  steps jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text
);

CREATE INDEX IF NOT EXISTS account_deletion_audit_created_idx
  ON public.account_deletion_audit (created_at DESC);

ALTER TABLE public.account_deletion_audit ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.account_deletion_audit FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.account_deletion_audit TO service_role;
