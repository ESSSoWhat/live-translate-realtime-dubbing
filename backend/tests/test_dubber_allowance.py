from app.services.dubber_allowance import translation_block, usage_payload


def test_free_trial_allows_three_videos_and_one_game():
    assert translation_block("free", "video", set(), "a") is None
    assert translation_block("free", "video", {"a", "b"}, "c") is None
    blocked = translation_block("free", "video", {"a", "b", "c"}, "d")
    assert blocked and "3 videos" in blocked
    assert translation_block("free", "video", {"a", "b", "c"}, "a") is None
    assert translation_block("free", "game", set(), "game-1") is None
    again = translation_block("free", "game", {"game-1"}, "game-2")
    assert again and "1 game" in again


def test_any_paid_plan_is_full_access():
    claimed = {"a", "b", "c", "d"}
    for tier in ("starter", "pro", "early_adopters"):
        assert translation_block(tier, "video", claimed, "new") is None
        assert translation_block(tier, "game", claimed, "new") is None
    payload = usage_payload("pro", claimed, {"game"})
    assert payload["paid"] is True
    assert payload["videos_limit"] is None
    assert payload["games_limit"] is None
