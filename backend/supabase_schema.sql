-- Live Translate — Supabase schema
-- Run this in the Supabase SQL Editor

                                                                                                                                                                        CREATE TABLE IF NOT EXISTS tier_limits (
    tier                TEXT PRIMARY KEY,
    dubbing_seconds     INT  NOT NULL,
    tts_chars           INT  NOT NULL,
    stt_seconds         INT  NOT NULL,
    translation_chars   INT  NOT NULL,
    voice_clones        INT  NOT NULL,
    price_monthly_usd   NUMERIC(10,2) NOT NULL DEFAULT 0,
    stripe_price_id     TEXT
);

-- Usage caps: free 30 min/mo, hobby 5 hr/mo, pro 15 hr/mo, early_adopters unlimited
-- Time in seconds; unlimited = max int (2^31 - 1)
INSERT INTO tier_limits (tier, dubbing_seconds, tts_chars, stt_seconds, translation_chars, voice_clones, price_monthly_usd, stripe_price_id) VALUES
  ('free',            1800,    50000,    1800,    50000,    1,   0.00,  NULL),   -- 30 min/month (free trial)
  ('starter',         18000,   500000,   18000,   500000,    5,   9.99,  NULL),   -- 5 hr/month (Hobby)
  ('pro',             54000,  2000000,   54000,  2000000,   20,  24.99,  NULL),   -- 15 hr/month (Pro)
  ('early_adopters', 2147483647, 2147483647, 2147483647, 2147483647, 99, 0.00, NULL)  -- unlimited
ON CONFLICT (tier) DO UPDATE SET
  dubbing_seconds = EXCLUDED.dubbing_seconds,
  tts_chars = EXCLUDED.tts_chars,
  stt_seconds = EXCLUDED.stt_seconds,
  translation_chars = EXCLUDED.translation_chars,
  voice_clones = EXCLUDED.voice_clones,
  price_monthly_usd = EXCLUDED.price_monthly_usd;

-- ── Users ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supabase_uid        UUID UNIQUE,
    email               TEXT NOT NULL UNIQUE,
    api_key             TEXT UNIQUE,
    stripe_customer_id  TEXT,
    tier                TEXT NOT NULL DEFAULT 'free' REFERENCES tier_limits(tier),
    subscription_status TEXT NOT NULL DEFAULT 'active',
    subscription_id     TEXT,
    phone_e164          TEXT UNIQUE,      -- verified caller number (translated calls)
    phone_verified_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_supabase_uid ON users(supabase_uid);
CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users(stripe_customer_id);

-- Trigger to auto-update updated_at on users
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── Usage records (one row per user per billing month) ────────────────────────
CREATE TABLE IF NOT EXISTS usage_records (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    dubbing_seconds     INT NOT NULL DEFAULT 0,
    tts_chars           INT NOT NULL DEFAULT 0,
    stt_seconds         INT NOT NULL DEFAULT 0,
    translation_chars   INT NOT NULL DEFAULT 0,
    voice_clones        INT NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, period_start),
    CONSTRAINT chk_period_end_after_start CHECK (period_end >= period_start)
);

CREATE INDEX IF NOT EXISTS idx_usage_user_period ON usage_records(user_id, period_start DESC);

-- ── Cloned voice ownership (ElevenLabs voice_id → user who created it) ───────
CREATE TABLE IF NOT EXISTS user_voices (
    voice_id     TEXT NOT NULL PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_user_voices_user_id ON user_voices(user_id);

-- ── Webhook idempotency (dedupe PayPal/Qonversion retries) ──────────────────
-- Unique event_id ensures a webhook event is provisioned at most once even if the
-- provider retries. Backend claims via INSERT ... ON CONFLICT DO NOTHING.
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id     TEXT NOT NULL PRIMARY KEY,
    source       TEXT NOT NULL DEFAULT 'paypal',
    event_type   TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_processed_at ON webhook_events(processed_at DESC);

-- ── Upgrade-nudge A/B analytics ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nudge_events (
    id          BIGSERIAL PRIMARY KEY,
    variant     TEXT NOT NULL,           -- 'a' | 'b'
    action      TEXT NOT NULL,           -- 'shown' | 'clicked'
    feature     TEXT,                    -- minutes | text-to-speech | translation
    surface     TEXT,                    -- mobile | wix | desktop
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_nudge_events_variant ON nudge_events(variant, action);

-- Desktop browser SSO handoff (shared across API workers; service role only)
CREATE TABLE IF NOT EXISTS desktop_handoffs (
    session_id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS desktop_handoffs_created_at_idx ON desktop_handoffs (created_at);

DROP TRIGGER IF EXISTS update_usage_records_updated_at ON usage_records;
CREATE TRIGGER update_usage_records_updated_at
    BEFORE UPDATE ON usage_records
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── Row-level security (RLS) ─────────────────────────────────────────────────
-- Backend uses the service role key which bypasses RLS.
-- These policies protect direct client access (e.g. Supabase Studio).
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_voices ENABLE ROW LEVEL SECURITY;
ALTER TABLE tier_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE nudge_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE desktop_handoffs ENABLE ROW LEVEL SECURITY;

-- Service role can do everything (RLS is bypassed for service role)
DROP POLICY IF EXISTS users_select_own ON users;
CREATE POLICY users_select_own ON users
    FOR SELECT USING (auth.uid() = supabase_uid);

DROP POLICY IF EXISTS usage_select_own ON usage_records;
CREATE POLICY usage_select_own ON usage_records
    FOR SELECT USING (
        user_id = (SELECT id FROM users WHERE supabase_uid = auth.uid())
    );

DROP POLICY IF EXISTS user_voices_select_own ON user_voices;
CREATE POLICY user_voices_select_own ON user_voices
    FOR SELECT USING (
        user_id = (SELECT id FROM users WHERE supabase_uid = auth.uid())
    );

CREATE POLICY tier_limits_public_read ON tier_limits
    FOR SELECT USING (true);

-- ── Phone verification state (hashed codes, attempts, send limits) ───────────
-- Backend service role only; RLS on with no policies. See migrations/003_phone_verification_codes.sql.
CREATE TABLE IF NOT EXISTS phone_verification_codes (
    user_id     UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    phone_e164  TEXT NOT NULL,
    code_hash   TEXT,
    expires_at  TIMESTAMPTZ NOT NULL,
    attempts    INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS phone_verification_sends (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    phone_e164  TEXT NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS phone_verification_sends_user_sent_idx ON phone_verification_sends (user_id, sent_at);
CREATE INDEX IF NOT EXISTS phone_verification_sends_phone_sent_idx ON phone_verification_sends (phone_e164, sent_at);

ALTER TABLE phone_verification_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE phone_verification_sends ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.try_record_phone_verification_send(
  p_user_id uuid,
  p_phone text,
  p_now timestamptz DEFAULT now()
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  last_sent timestamptz;
  oldest timestamptz;
  cnt integer;
  new_id uuid;
  gap interval := interval '60 seconds';
  window_len interval := interval '1 hour';
BEGIN
  IF p_user_id::text < p_phone THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('pv:u:' || p_user_id::text, 0));
    PERFORM pg_advisory_xact_lock(hashtextextended('pv:p:' || p_phone, 0));
  ELSE
    PERFORM pg_advisory_xact_lock(hashtextextended('pv:p:' || p_phone, 0));
    PERFORM pg_advisory_xact_lock(hashtextextended('pv:u:' || p_user_id::text, 0));
  END IF;

  DELETE FROM public.phone_verification_sends WHERE sent_at <= p_now - window_len;

  SELECT max(sent_at) INTO last_sent
  FROM public.phone_verification_sends
  WHERE user_id = p_user_id OR phone_e164 = p_phone;

  IF last_sent IS NOT NULL AND last_sent > p_now - gap THEN
    RETURN jsonb_build_object(
      'status', 'resend_gap',
      'retry_after', GREATEST(1, CEIL(EXTRACT(epoch FROM (last_sent + gap - p_now))))::int
    );
  END IF;

  SELECT count(*), min(sent_at) INTO cnt, oldest
  FROM public.phone_verification_sends
  WHERE user_id = p_user_id AND sent_at > p_now - window_len;
  IF cnt >= 5 THEN
    RETURN jsonb_build_object(
      'status', 'hourly_limit',
      'retry_after', GREATEST(1, CEIL(EXTRACT(epoch FROM (oldest + window_len - p_now))))::int
    );
  END IF;

  SELECT count(*), min(sent_at) INTO cnt, oldest
  FROM public.phone_verification_sends
  WHERE phone_e164 = p_phone AND sent_at > p_now - window_len;
  IF cnt >= 5 THEN
    RETURN jsonb_build_object(
      'status', 'hourly_limit',
      'retry_after', GREATEST(1, CEIL(EXTRACT(epoch FROM (oldest + window_len - p_now))))::int
    );
  END IF;

  INSERT INTO public.phone_verification_sends (user_id, phone_e164, sent_at)
  VALUES (p_user_id, p_phone, p_now)
  RETURNING id INTO new_id;

  RETURN jsonb_build_object('status', 'ok', 'send_id', new_id);
END;
$$;

CREATE OR REPLACE FUNCTION public.apply_phone_verification_attempt(
  p_user_id uuid,
  p_phone text,
  p_code_hash text DEFAULT NULL,
  p_approved boolean DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  rec public.phone_verification_codes%ROWTYPE;
  matched boolean;
BEGIN
  SELECT * INTO rec
  FROM public.phone_verification_codes
  WHERE user_id = p_user_id
  FOR UPDATE;

  IF NOT FOUND OR rec.phone_e164 IS DISTINCT FROM p_phone THEN
    RETURN 'missing';
  END IF;

  IF rec.expires_at <= now() THEN
    DELETE FROM public.phone_verification_codes WHERE user_id = p_user_id;
    RETURN 'expired';
  END IF;

  IF rec.attempts >= 5 THEN
    RETURN 'too_many';
  END IF;

  IF p_approved IS TRUE THEN
    matched := TRUE;
  ELSIF p_approved IS FALSE THEN
    matched := FALSE;
  ELSE
    matched := (p_code_hash IS NOT NULL AND rec.code_hash IS NOT NULL AND rec.code_hash = p_code_hash);
  END IF;

  IF matched THEN
    DELETE FROM public.phone_verification_codes WHERE user_id = p_user_id;
    RETURN 'ok';
  END IF;

  UPDATE public.phone_verification_codes
  SET attempts = attempts + 1
  WHERE user_id = p_user_id;
  RETURN 'invalid';
END;
$$;
