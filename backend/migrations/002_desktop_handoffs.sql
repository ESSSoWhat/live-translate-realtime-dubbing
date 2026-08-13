-- Migration: desktop login handoff table (device-code style sign-in)
-- Run once in Supabase SQL Editor (or psql) against your project.
-- Idempotent: safe to run multiple times.
--
-- Purpose: the desktop app cannot receive a Wix->localhost redirect, so it
-- generates a one-time `code`, opens the browser to Wix, and after login the
-- Wix backend posts the member's api_key here. The desktop polls for it once.

CREATE TABLE IF NOT EXISTS public.desktop_handoffs (
    code        TEXT PRIMARY KEY,
    api_key     TEXT NOT NULL,
    user_id     TEXT,
    tier        TEXT NOT NULL DEFAULT 'free',
    email       TEXT,
    consumed    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for TTL/expiry filtering on the poll path.
CREATE INDEX IF NOT EXISTS idx_desktop_handoffs_created_at
    ON public.desktop_handoffs (created_at);

-- Lock the table down: only the backend (service role) touches it. Service role
-- bypasses RLS, and no anon/authenticated policies are added, so it is not
-- readable by site visitors even though it briefly holds an api_key.
ALTER TABLE public.desktop_handoffs ENABLE ROW LEVEL SECURITY;

-- Optional housekeeping: delete stale handoffs older than 1 day. Safe to run
-- periodically (e.g. a scheduled job) or manually.
-- DELETE FROM public.desktop_handoffs WHERE created_at < NOW() - INTERVAL '1 day';
