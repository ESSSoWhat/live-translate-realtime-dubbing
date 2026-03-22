"""Usage quota checking and recording."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# Maps event_type to (usage_column, limit_column)
_COLUMN_MAP: dict[str, str] = {
    "stt": "stt_seconds",
    "tts": "tts_chars",
    "dub": "dubbing_seconds",
    "translate": "translation_chars",
    "clone": "voice_clones",
}

_db_pool: asyncpg.Pool | None = None
_db_pool_lock = asyncio.Lock()


async def get_db_pool() -> asyncpg.Pool:
    """Return the shared asyncpg connection pool, creating it if needed."""
    global _db_pool
    if _db_pool is None:
        async with _db_pool_lock:
            if _db_pool is None:
                from app.config import get_settings
                cfg = get_settings()
                dsn = cfg.supabase_db_url
                if not dsn:
                    raise RuntimeError(
                        "SUPABASE_DB_URL environment variable not set. "
                        "Set it to your Supabase PostgreSQL connection string "
                        "(e.g., postgresql://postgres:password@db.xxx.supabase.co:5432/postgres)"
                    )
                _db_pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
                logger.info("Database pool created")
    return _db_pool


def _period_start() -> date:
    """First day of the current calendar month."""
    today = date.today()
    return today.replace(day=1)


def _period_end() -> date:
    """Last day of the current calendar month."""
    today = date.today()
    if today.month == 12:
        return date(today.year + 1, 1, 1) - timedelta(days=1)
    return date(today.year, today.month + 1, 1) - timedelta(days=1)


async def check_quota(user_id: str, event_type: str, quantity: int) -> None:
    """
    Check quota for user and event type; raise if exceeded.

    Raises LookupError when user lookup fails.
    Raises QuotaExceededError when the user has exceeded their quota (results in HTTP 402 upstream).
    """
    col = _COLUMN_MAP.get(event_type)
    if col is None:
        return  # unknown type — allow

    pool = await get_db_pool()
    period = _period_start()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COALESCE(ur.{col}, 0) AS used,
                tl.{col}              AS limit_val
            FROM users u
            JOIN tier_limits tl ON tl.tier = u.tier
            LEFT JOIN usage_records ur
                ON ur.user_id = u.id AND ur.period_start = $2
            WHERE u.id = $1
            """.replace("{col}", col),
            user_id, period,
        )

    if row is None:
        raise LookupError(f"User {user_id} not found")

    used: int = row["used"]
    limit_val: int = row["limit_val"]

    if used + quantity > limit_val:
        raise QuotaExceededError(
            event_type=event_type,
            used=used,
            limit=limit_val,
            requested=quantity,
        )


async def check_and_record_quota(user_id: str, event_type: str, quantity: int) -> None:
    """
    Atomically check quota and record usage in one transaction.
    Raises LookupError if user not found, QuotaExceededError if increment would exceed limit.
    """
    col = _COLUMN_MAP.get(event_type)
    if col is None or quantity <= 0:
        return

    pool = await get_db_pool()
    period = _period_start()
    period_end = _period_end()

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT
                    COALESCE(ur.{col}, 0) AS used,
                    tl.{col}              AS limit_val
                FROM users u
                JOIN tier_limits tl ON tl.tier = u.tier
                LEFT JOIN usage_records ur
                    ON ur.user_id = u.id AND ur.period_start = $2
                WHERE u.id = $1
                FOR UPDATE OF u
                """.replace("{col}", col),
                user_id, period,
            )
            if row is None:
                raise LookupError(f"User {user_id} not found")
            used = int(row["used"])
            limit_val = int(row["limit_val"])
            if used + quantity > limit_val:
                raise QuotaExceededError(
                    event_type=event_type,
                    used=used,
                    limit=limit_val,
                    requested=quantity,
                )
            await conn.execute(
                f"""
                INSERT INTO usage_records (user_id, period_start, period_end, {col})
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, period_start)
                DO UPDATE SET {col} = usage_records.{col} + EXCLUDED.{col},
                              updated_at = NOW()
                """,
                user_id, period, period_end, quantity,
            )
    logger.info(
        "usage_recorded",
        user_id=user_id,
        event_type=event_type,
        quantity=quantity,
        column=col,
    )


async def record_usage(user_id: str, event_type: str, quantity: int) -> None:
    """Increment usage for the current billing period (no quota check). Prefer check_and_record_quota for atomic check+record."""
    col = _COLUMN_MAP.get(event_type)
    if col is None or quantity <= 0:
        return
    pool = await get_db_pool()
    period = _period_start()
    period_end = _period_end()
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO usage_records (user_id, period_start, period_end, {col})
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, period_start)
            DO UPDATE SET {col} = usage_records.{col} + EXCLUDED.{col},
                          updated_at = NOW()
            """,
            user_id, period, period_end, quantity,
        )
    logger.info(
        "usage_recorded",
        user_id=user_id,
        event_type=event_type,
        quantity=quantity,
        column=col,
    )


async def get_usage_snapshot(user_id: str) -> dict:
    """Return current usage and limits for a user."""
    pool = await get_db_pool()
    period = _period_start()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COALESCE(ur.dubbing_seconds, 0)  AS dub_used,
                COALESCE(ur.tts_chars, 0)        AS tts_used,
                COALESCE(ur.stt_seconds, 0)      AS stt_used,
                COALESCE(ur.translation_chars, 0) AS translation_used,
                COALESCE(ur.voice_clones, 0)     AS clones_used,
                tl.dubbing_seconds               AS dub_limit,
                tl.tts_chars                     AS tts_limit,
                tl.stt_seconds                   AS stt_limit,
                tl.translation_chars             AS translation_limit,
                tl.voice_clones                  AS clones_limit
            FROM users u
            JOIN tier_limits tl ON tl.tier = u.tier
            LEFT JOIN usage_records ur
                ON ur.user_id = u.id AND ur.period_start = $2
            WHERE u.id = $1
            """,
            user_id, period,
        )

    if row is None:
        raise LookupError(f"User {user_id} not found")

    next_month = _period_end() + timedelta(days=1)

    return {
        "dubbing_seconds_used": row["dub_used"],
        "dubbing_seconds_limit": row["dub_limit"],
        "tts_chars_used": row["tts_used"],
        "tts_chars_limit": row["tts_limit"],
        "stt_seconds_used": row["stt_used"],
        "stt_seconds_limit": row["stt_limit"],
        "translation_chars_used": row["translation_used"],
        "translation_chars_limit": row["translation_limit"],
        "voice_clones_used": row["clones_used"],
        "voice_clones_limit": row["clones_limit"],
        "period_reset_date": str(next_month),
    }


class QuotaExceededError(Exception):
    """Raised when a user's usage would exceed their tier limit."""

    def __init__(self, event_type: str, used: int, limit: int, requested: int) -> None:
        self.event_type = event_type
        self.used = used
        self.limit = limit
        self.requested = requested
        super().__init__(f"Quota exceeded: {used}/{limit} {event_type} used, requested {requested}")
