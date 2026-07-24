"""Standalone integration test for subscription-based usage tracking.

Runs against a real PostgreSQL instance seeded with supabase_schema.sql.
Not a pytest module (repo has no pytest-asyncio); run directly:

    SUPABASE_DB_URL=postgresql://postgres:postgres@127.0.0.1:5432/lt_test \
        python tests/test_usage_tracking_integration.py

Exits 0 on all-pass, 1 on any failure.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import asyncpg

import app.services.usage as usage
from app.services.usage import (
    QuotaExceededError,
    check_and_record_quota,
    check_quota,
    get_usage_snapshot,
    record_usage,
)

DB_URL = os.environ.get("SUPABASE_DB_URL", "")

_passed = 0
_failed = 0


def _ok(name: str) -> None:
    global _passed
    _passed += 1
    print(f"  PASS  {name}")


def _fail(name: str, err: str) -> None:
    global _failed
    _failed += 1
    print(f"  FAIL  {name}: {err}")


async def _make_user(tier: str) -> str:
    conn = await asyncpg.connect(DB_URL)
    uid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO users (id, email, tier, api_key) VALUES ($1, $2, $3, $4)",
        uid, f"{tier}-{uid[:8]}@test.local", tier, f"key_{uid[:8]}",
    )
    await conn.close()
    return uid


async def _cleanup(uid: str) -> None:
    conn = await asyncpg.connect(DB_URL)
    await conn.execute("DELETE FROM users WHERE id = $1", uid)
    await conn.close()


async def _expect_raises(exc_type, coro) -> None:
    try:
        await coro
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__} not raised")


# ── Tests ───────────────────────────────────────────────────────────────────


async def test_free_tier_dub_quota_enforced():
    uid = await _make_user("free")
    try:
        await check_and_record_quota(uid, "dub", 1800)  # exactly at limit
        snap = await get_usage_snapshot(uid)
        assert snap["dubbing_seconds_used"] == 1800
        assert snap["dubbing_seconds_limit"] == 1800
        await _expect_raises(QuotaExceededError, check_and_record_quota(uid, "dub", 1))
        snap = await get_usage_snapshot(uid)
        assert snap["dubbing_seconds_used"] == 1800, "usage changed after rejected request"
    finally:
        await _cleanup(uid)


async def test_starter_tier_tts_quota():
    uid = await _make_user("starter")
    try:
        await check_and_record_quota(uid, "tts", 400000)
        await _expect_raises(QuotaExceededError, check_and_record_quota(uid, "tts", 200000))
        await check_and_record_quota(uid, "tts", 100000)  # exactly 500k
        snap = await get_usage_snapshot(uid)
        assert snap["tts_chars_used"] == 500000
        assert snap["tts_chars_limit"] == 500000
    finally:
        await _cleanup(uid)


async def test_pro_tier_all_event_types():
    uid = await _make_user("pro")
    try:
        await check_and_record_quota(uid, "dub", 1000)
        await check_and_record_quota(uid, "tts", 5000)
        await check_and_record_quota(uid, "stt", 2000)
        await check_and_record_quota(uid, "translate", 3000)
        await check_and_record_quota(uid, "clone", 2)
        snap = await get_usage_snapshot(uid)
        assert snap["dubbing_seconds_used"] == 1000
        assert snap["tts_chars_used"] == 5000
        assert snap["stt_seconds_used"] == 2000
        assert snap["translation_chars_used"] == 3000
        assert snap["voice_clones_used"] == 2
        assert snap["dubbing_seconds_limit"] == 54000
    finally:
        await _cleanup(uid)


async def test_early_adopters_unlimited():
    uid = await _make_user("early_adopters")
    try:
        await check_and_record_quota(uid, "dub", 100_000_000)
        snap = await get_usage_snapshot(uid)
        assert snap["dubbing_seconds_used"] == 100_000_000
        assert snap["dubbing_seconds_limit"] == 2147483647
    finally:
        await _cleanup(uid)


async def test_check_quota_does_not_record():
    uid = await _make_user("free")
    try:
        await check_quota(uid, "dub", 500)
        snap = await get_usage_snapshot(uid)
        assert snap["dubbing_seconds_used"] == 0, "check_quota must not record"
        await _expect_raises(QuotaExceededError, check_quota(uid, "dub", 2000))
    finally:
        await _cleanup(uid)


async def test_unknown_event_type_is_noop():
    uid = await _make_user("free")
    try:
        await check_and_record_quota(uid, "bogus", 999)
        await check_quota(uid, "bogus", 999)
        snap = await get_usage_snapshot(uid)
        assert snap["dubbing_seconds_used"] == 0
    finally:
        await _cleanup(uid)


async def test_user_not_found_raises_lookup():
    await _expect_raises(LookupError, get_usage_snapshot(str(uuid.uuid4())))
    await _expect_raises(LookupError, check_and_record_quota(str(uuid.uuid4()), "dub", 1))


async def test_record_usage_accumulates():
    uid = await _make_user("free")
    try:
        await record_usage(uid, "tts", 100)
        await record_usage(uid, "tts", 250)
        snap = await get_usage_snapshot(uid)
        assert snap["tts_chars_used"] == 350
    finally:
        await _cleanup(uid)


async def test_concurrent_requests_respect_limit():
    uid = await _make_user("free")  # dub limit 1800
    try:
        async def one() -> bool:
            try:
                await check_and_record_quota(uid, "dub", 100)
                return True
            except QuotaExceededError:
                return False

        results = await asyncio.gather(*[one() for _ in range(20)])
        succeeded = sum(results)
        snap = await get_usage_snapshot(uid)
        assert snap["dubbing_seconds_used"] <= 1800, "limit exceeded under concurrency"
        assert snap["dubbing_seconds_used"] == succeeded * 100
        assert succeeded == 18, f"expected 18 successes (1800/100), got {succeeded}"
    finally:
        await _cleanup(uid)


async def test_period_reset_date_present():
    uid = await _make_user("free")
    try:
        snap = await get_usage_snapshot(uid)
        assert snap["period_reset_date"], "missing period_reset_date"
        # Reset date is the first day of next month.
        assert snap["period_reset_date"].endswith("-01")
    finally:
        await _cleanup(uid)


TESTS = [
    test_free_tier_dub_quota_enforced,
    test_starter_tier_tts_quota,
    test_pro_tier_all_event_types,
    test_early_adopters_unlimited,
    test_check_quota_does_not_record,
    test_unknown_event_type_is_noop,
    test_user_not_found_raises_lookup,
    test_record_usage_accumulates,
    test_concurrent_requests_respect_limit,
    test_period_reset_date_present,
]


async def main() -> int:
    if not DB_URL:
        print("SKIP: SUPABASE_DB_URL not set")
        return 0
    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP: DB unreachable ({exc})")
        return 0

    print("Usage-tracking integration tests:")
    for t in TESTS:
        usage._db_pool = None  # noqa: SLF001  fresh pool per test
        try:
            await t()
            _ok(t.__name__)
        except Exception as exc:  # noqa: BLE001
            _fail(t.__name__, repr(exc))
        finally:
            if usage._db_pool is not None:  # noqa: SLF001
                await usage._db_pool.close()  # noqa: SLF001
                usage._db_pool = None  # noqa: SLF001

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
