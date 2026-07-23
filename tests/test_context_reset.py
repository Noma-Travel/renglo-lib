"""ChatController: per-thread context reset and the turn-time cutoff it drives.

These live here rather than in noma's handler suite because that suite's
conftest stubs the whole `renglo` package with MagicMocks — importing
ChatController there would exercise a mock instead of this code.

Run with the app's venv (the one whose editable install points at this repo):

    system/venv/Scripts/python -m pytest dev/renglo-lib/tests
"""
from unittest.mock import MagicMock

from renglo.chat.chat_controller import ChatController

ACTIVE_TRIP = "irn:active_trip"
SUMMARIES = "irn:history_summaries"
RESET = "irn:context_reset"
TOKENS = "irn:token_usage"


def _chc():
    """A ChatController without __init__ (it wants live AWS/Flask config)."""
    return ChatController.__new__(ChatController)


def _turn(turn_id, time):
    return {"_id": turn_id, "time": str(time), "messages": []}


# --- the cutoff ------------------------------------------------------------


def test_turns_after_keeps_only_newer_turns():
    items = [_turn("a", 100.0), _turn("b", 200.0), _turn("c", 300.0)]
    assert [t["_id"] for t in ChatController._turns_after(items, 200.0)] == ["c"]


def test_turns_after_is_exclusive_at_the_cutoff():
    # The reset stamps "now": a turn written at that exact instant belongs to
    # what the user just cleared, so it must not survive.
    assert ChatController._turns_after([_turn("a", 150.0)], 150.0) == []


def test_turns_after_compares_numerically_not_lexicographically():
    # As strings, "1000000000.0" < "999999999.0" — a lexicographic compare
    # would keep the OLD turn and drop the new one.
    items = [_turn("old", 999999999.0), _turn("new", 1000000000.0)]
    assert [t["_id"] for t in ChatController._turns_after(items, 999999999.5)] == ["new"]


def test_turns_after_keeps_turns_with_an_unusable_time():
    # Losing conversation is worse than showing one turn too many.
    items = [_turn("bad", "not-a-number"), {"_id": "missing"}]
    assert [t["_id"] for t in ChatController._turns_after(items, 100.0)] == ["bad", "missing"]


def test_list_turns_without_since_returns_everything():
    chc = _chc()
    chc.CHM = MagicMock()
    chc.CHM.query_chat.return_value = {"success": True, "items": [_turn("a", 1.0), _turn("b", 2.0)]}
    assert [t["_id"] for t in chc.list_turns("p", "o", "et", "e", "t")["items"]] == ["a", "b"]


def test_list_turns_with_since_hides_older_turns():
    chc = _chc()
    chc.CHM = MagicMock()
    chc.CHM.query_chat.return_value = {"success": True, "items": [_turn("a", 1.0), _turn("b", 2.0)]}
    out = chc.list_turns("p", "o", "et", "e", "t", since=1.5)
    assert [t["_id"] for t in out["items"]] == ["b"]


# --- reset_thread_context --------------------------------------------------


def _reset_chc(cache):
    chc = _chc()
    chc.list_workspaces = MagicMock(
        return_value={"success": True, "items": [{"_id": "ws-1", "cache": dict(cache)}]}
    )
    chc.update_workspace = MagicMock(return_value={"success": True})
    return chc


def _written_cache(chc):
    # update_workspace(portfolio, org, entity_type, entity_id, thread, ws_id, payload)
    return chc.update_workspace.call_args[0][6]["cache"]


def test_reset_preserves_the_active_trip_pointer():
    """The product requirement: clearing the chat must never unlink the trip.

    Guards the trap that update_workspace REPLACES the whole cache — writing
    the marker without merging would drop this pointer.
    """
    chc = _reset_chc({ACTIVE_TRIP: "trip-123"})
    out = chc.reset_thread_context("p", "o", "et", "e", "t")
    assert out["success"]
    assert _written_cache(chc)[ACTIVE_TRIP] == "trip-123"


def test_reset_drops_history_summaries():
    # A summary's covers_up_to indexes the PRE-reset history; surviving a reset
    # it would address a list that just shrank and corrupt assembly silently.
    chc = _reset_chc({SUMMARIES: [{"summary_text": "x", "covers_up_to": 4}]})
    chc.reset_thread_context("p", "o", "et", "e", "t")
    assert SUMMARIES not in _written_cache(chc)


def test_reset_stamps_a_cutoff_and_returns_it():
    chc = _reset_chc({})
    out = chc.reset_thread_context("p", "o", "et", "e", "t")
    marker = _written_cache(chc)[RESET]
    assert float(marker["since"]) > 0
    assert marker["since"] == out["since"]


def test_reset_zeroes_the_since_reset_tokens_but_keeps_the_lifetime_total():
    chc = _reset_chc({TOKENS: {"total": {"total_tokens": 900}, "since_reset": {"total_tokens": 400}}})
    chc.reset_thread_context("p", "o", "et", "e", "t")
    usage = _written_cache(chc)[TOKENS]
    assert usage["since_reset"] == {}
    assert usage["total"]["total_tokens"] == 900


def test_reset_zeroes_the_live_context_meter():
    # last_prompt_tokens is what the UI badge actually renders. Leaving it
    # untouched made the badge show the pre-reset context size until the next
    # turn — i.e. the reset looked like a no-op.
    chc = _reset_chc({TOKENS: {"last_prompt_tokens": 9000,
                               "total": {"total_tokens": 900},
                               "since_reset": {"total_tokens": 400}}})
    chc.reset_thread_context("p", "o", "et", "e", "t")
    usage = _written_cache(chc)[TOKENS]
    assert usage["last_prompt_tokens"] == 0


def test_reset_without_a_workspace_is_a_noop_not_a_failure():
    # No workspace = the agent never ran = no turns to hide.
    chc = _chc()
    chc.list_workspaces = MagicMock(return_value={"success": True, "items": []})
    chc.update_workspace = MagicMock()
    out = chc.reset_thread_context("p", "o", "et", "e", "t")
    assert out["success"] and out["reset"] == 0
    chc.update_workspace.assert_not_called()


# --- get_context_reset_since -----------------------------------------------


def test_get_context_reset_since_reads_the_marker():
    chc = _chc()
    chc.list_workspaces = MagicMock(
        return_value={"success": True, "items": [{"_id": "w", "cache": {RESET: {"since": "150.5"}}}]}
    )
    assert chc.get_context_reset_since("p", "o", "et", "e", "t") == 150.5


def test_get_context_reset_since_is_none_when_never_reset():
    chc = _chc()
    chc.list_workspaces = MagicMock(return_value={"success": True, "items": [{"_id": "w", "cache": {}}]})
    assert chc.get_context_reset_since("p", "o", "et", "e", "t") is None
