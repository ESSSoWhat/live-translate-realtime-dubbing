"""Trial cap for DubTool. Free accounts may translate 3 videos and 1 game."""

from __future__ import annotations

FREE_VIDEO_LIMIT = 3
FREE_GAME_LIMIT = 1

_TABLE = """
CREATE TABLE IF NOT EXISTS dubber_translations (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('video', 'game')),
    source_key  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, kind, source_key)
)
"""


def translation_block(tier: str, kind: str, claimed: set[str], source_key: str) -> str | None:
    """Why a free account cannot translate this source. Any other tier is full access."""
    if (tier or "free").strip().lower() != "free":
        return None
    if source_key in claimed:
        return None
    if kind == "video":
        limit, label = FREE_VIDEO_LIMIT, "videos"
    elif kind == "game":
        limit, label = FREE_GAME_LIMIT, "game" if FREE_GAME_LIMIT == 1 else "games"
    else:
        return "Unknown translation type"
    if len(claimed) >= limit:
        return (
            f"The free trial includes {limit} {label}. "
            "Any paid plan on livetranslate.net unlocks the full app."
        )
    return None


def usage_payload(tier: str, videos: set[str], games: set[str]) -> dict:
    paid = (tier or "free").strip().lower() != "free"
    return {
        "allowed": True,
        "tier": tier or "free",
        "paid": paid,
        "videos_used": len(videos),
        "games_used": len(games),
        "videos_limit": None if paid else FREE_VIDEO_LIMIT,
        "games_limit": None if paid else FREE_GAME_LIMIT,
    }


async def claim_translation(user_id: str, tier: str, kind: str, source_key: str) -> dict:
    """Record one source for this account, or refuse when the free trial is used up."""
    if kind not in ("video", "game"):
        raise ValueError("kind must be video or game")
    source_key = (source_key or "").strip()
    if len(source_key) < 16:
        raise ValueError("source_key is too short")

    from app.services.usage import get_db_pool

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(_TABLE)
        rows = await conn.fetch(
            "SELECT kind, source_key FROM dubber_translations WHERE user_id = $1",
            user_id,
        )
        videos = {row["source_key"] for row in rows if row["kind"] == "video"}
        games = {row["source_key"] for row in rows if row["kind"] == "game"}
        claimed = videos if kind == "video" else games
        blocked = translation_block(tier, kind, claimed, source_key)
        if blocked:
            raise TrialLimit(blocked)
        if source_key not in claimed:
            await conn.execute(
                """
                INSERT INTO dubber_translations (user_id, kind, source_key)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, kind, source_key) DO NOTHING
                """,
                user_id,
                kind,
                source_key,
            )
            claimed.add(source_key)
        if kind == "video":
            videos = claimed
        else:
            games = claimed
    return usage_payload(tier, videos, games)


class TrialLimit(Exception):
    """The free trial cannot translate another video or game."""
