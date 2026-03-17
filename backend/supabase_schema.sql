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
