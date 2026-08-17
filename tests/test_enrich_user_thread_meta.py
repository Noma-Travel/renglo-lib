"""Inbox thread meta enrich: FE/BE public_user mismatch + legacy entity_index."""
from unittest.mock import MagicMock

from renglo.chat.chat_controller import ChatController


PORTFOLIO = "pf"
ORG = "org1"
ENTITY_TYPE = "org-trip"
THREAD = "thread-abc"


def _cc_with_rows(rows_by_prefix):
    cc = ChatController(config={})
    chm = MagicMock()

    def query_chat(_index, prefix, limit=1000, sort="asc"):
        return {"success": True, "items": list(rows_by_prefix.get(prefix, []))}

    chm.query_chat.side_effect = query_chat
    chm.update_chat.return_value = {"success": True}
    cc.CHM = chm
    return cc


def test_update_thread_meta_finds_new_entity_index():
    container = f"{ORG}-u:md5user"
    row = {
        "_id": THREAD,
        "entity_id": container,
        "entity_index": f"{container}/{THREAD}",
        "title": "",
        "index": "irn:chat:x",
    }
    cc = _cc_with_rows({f"{container}/{THREAD}": [row]})
    out = cc.update_thread_meta(
        PORTFOLIO, ORG, ENTITY_TYPE, container, THREAD,
        last_message="oi",
    )
    assert out["success"] is True
    assert out["updated"] == 1
    written = cc.CHM.update_chat.call_args[0][0]
    assert written["last_message"] == "oi"


def test_update_thread_meta_finds_legacy_entity_index():
    container = f"{ORG}-u:md5user"
    row = {
        "_id": THREAD,
        "entity_id": container,
        "entity_index": container,  # legacy: no /thread_id
        "title": "",
        "index": "irn:chat:x",
    }
    # New-format prefix miss; container prefix hits legacy row.
    cc = _cc_with_rows({
        f"{container}/{THREAD}": [],
        container: [row],
    })
    out = cc.update_thread_meta(
        PORTFOLIO, ORG, ENTITY_TYPE, container, THREAD,
        fill_if_empty={"title": "hello"},
    )
    assert out["success"] is True
    written = cc.CHM.update_chat.call_args[0][0]
    assert written["title"] == "hello"


def test_update_thread_meta_rejects_other_entity_id():
    container = f"{ORG}-u:md5user"
    other = f"{ORG}-u:other"
    row = {
        "_id": THREAD,
        "entity_id": other,
        "entity_index": container,  # would only appear via broad prefix
        "title": "",
        "index": "irn:chat:x",
    }
    cc = _cc_with_rows({
        f"{container}/{THREAD}": [],
        container: [row],
    })
    out = cc.update_thread_meta(
        PORTFOLIO, ORG, ENTITY_TYPE, container, THREAD,
        last_message="x",
    )
    assert out["success"] is False
    assert out["message"] == "Thread not found"
    cc.CHM.update_chat.assert_not_called()


def test_enrich_user_thread_meta_falls_back_to_fe_sub():
    md5 = "ba7733482"
    sub = "04e88438-4031-7052-1980-23628dbbe457"
    fe_container = f"{ORG}-u:{sub}"
    row = {
        "_id": THREAD,
        "entity_id": fe_container,
        "entity_index": f"{fe_container}/{THREAD}",
        "title": "",
        "index": "irn:chat:x",
    }
    cc = _cc_with_rows({
        f"{ORG}-u:{md5}/{THREAD}": [],
        f"{ORG}-u:{md5}": [],
        f"{fe_container}/{THREAD}": [row],
    })
    out = cc.enrich_user_thread_meta(
        PORTFOLIO, ORG, ENTITY_TYPE, THREAD, [md5, sub],
        last_message="outra trip",
    )
    assert out["success"] is True
    assert out["public_user"] == sub
    assert out["container"] == fe_container
    written = cc.CHM.update_chat.call_args[0][0]
    assert written["last_message"] == "outra trip"


def test_enrich_user_thread_meta_does_not_create_on_miss():
    cc = _cc_with_rows({})
    out = cc.enrich_user_thread_meta(
        PORTFOLIO, ORG, ENTITY_TYPE, THREAD, ["md5", "sub-uuid"],
        last_message="x",
    )
    assert out["success"] is False
    assert out["message"] == "Thread not found"
    cc.CHM.update_chat.assert_not_called()
