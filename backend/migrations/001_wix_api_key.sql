-- Migration: Wix auth — nullable supabase_uid, add api_key
-- Run once in Supabase SQL Editor (or psql) against your project.
-- Idempotent: safe to run multiple times.

-- 1. Allow users without Supabase (Wix-only sign-in)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'supabase_uid'
  ) THEN
    ALTER TABLE public.users ALTER COLUMN supabase_uid DROP NOT NULL;
  END IF;
EXCEPTION
  WHEN others THEN
    NULL; -- Column may already be nullable
END $$;

-- 2. Add api_key column if missing
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'api_key'
  ) THEN
    ALTER TABLE public.users ADD COLUMN api_key TEXT UNIQUE;
  END IF;
END $$;
