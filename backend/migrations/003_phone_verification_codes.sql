-- Migration: shared SMS verification state (pending codes, attempts, send limits)
-- Run once in Supabase SQL Editor (or psql) after 002_verified_phone.sql.
-- Idempotent: safe to run multiple times.
-- Codes are stored as SHA-256 hashes only (never plaintext).
-- RLS is on with no policies: the backend service role bypasses RLS; clients cannot read or write.

CREATE TABLE IF NOT EXISTS public.phone_verification_codes (
  user_id uuid PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  phone_e164 text NOT NULL,
  -- NULL when Twilio Verify holds the secret; otherwise SHA-256 hex of "{phone}:{code}"
  code_hash text,
  expires_at timestamptz NOT NULL,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.phone_verification_sends (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  phone_e164 text NOT NULL,
  sent_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS phone_verification_sends_user_sent_idx
  ON public.phone_verification_sends (user_id, sent_at);
CREATE INDEX IF NOT EXISTS phone_verification_sends_phone_sent_idx
  ON public.phone_verification_sends (phone_e164, sent_at);

ALTER TABLE public.phone_verification_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phone_verification_sends ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.phone_verification_codes FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.phone_verification_sends FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.phone_verification_codes TO service_role;
GRANT ALL ON TABLE public.phone_verification_sends TO service_role;

-- Atomically enforce the 60-second resend gap and 5-sends-per-hour limits
-- (per user and per phone), then insert a send row. Returns jsonb:
--   {"status":"ok","send_id":"<uuid>"}
--   {"status":"resend_gap","retry_after":<seconds>}
--   {"status":"hourly_limit","retry_after":<seconds>}
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

-- Atomically check, increment attempts, or consume a pending verification.
-- p_approved TRUE/FALSE is the Twilio Verify path; NULL compares p_code_hash.
-- Returns: ok | missing | expired | too_many | invalid
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

REVOKE ALL ON FUNCTION public.try_record_phone_verification_send(uuid, text, timestamptz) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.apply_phone_verification_attempt(uuid, text, text, boolean) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.try_record_phone_verification_send(uuid, text, timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.apply_phone_verification_attempt(uuid, text, text, boolean) TO service_role;
