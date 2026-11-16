-- Migration: verified caller phone number for translated calls
-- Run once in Supabase SQL Editor (or psql). Idempotent: safe to run multiple times.
-- One number belongs to one account (UNIQUE allows many NULLs).

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS phone_e164 TEXT;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS phone_verified_at TIMESTAMPTZ;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'users_phone_e164_key'
  ) THEN
    ALTER TABLE public.users ADD CONSTRAINT users_phone_e164_key UNIQUE (phone_e164);
  END IF;
END $$;
