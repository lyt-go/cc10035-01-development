"""试投回单工作流测试：覆盖勾选审阅、规则停用冲突、重审与幂等签发全流程。

场景还原需求：
- R1 将选中帧解释为整数 0，R2 同时可解释成数字 0.0，另一帧未选；
- 操作人确认 R1 的试投后等待路由答复，管理员停用 R1，停用与这笔未发送试投冲突；
- 回单转「需要重审」，改用 R2 后呈现不同解释并由同一操作人再次确认；
- 同一申请重复提交只产生一次路由投递，路由详情只有一笔且与最终解释相符。
"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.store import store
from app.services.msg_decode import MsgDecodeService
from app.services.trial_receipt import (
    REVIEW_REQUIRED,
    STATUS_CONFIRMED,
    STATUS_DRAFT,
    STATUS_SENT,
    TrialReceiptService,
)


def _reset_store() -> None:
    store._tables = {
        name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
    }
    store._tables.setdefault("trial_receipt", [])
    store._tables.setdefault("trial_delivery", [])


@pytest.fixture(autouse=True)
def fresh_store():
    _reset_store()
    yield
    _reset_store()


@pytest.fixture
def service():
    return TrialReceiptService()


@pytest.fixture
def decode_service():
    return MsgDecodeService()


def _create_two_frame_receipt(service: TrialReceiptService):
    # 两帧原始报文：拟用 R1；第二帧将保持未勾选、留在草稿中。
    receipt, message = service.create_receipt(
        operator="值班员甲",
        frames=[
            {"raw_hex": "AA00", "rule_code": "R1"},
            {"raw_hex": "AA01", "rule_code": "R1"},
        ],
    )
    assert receipt is not None, message
    return receipt


def test_r1_interprets_integer_zero_and_r2_decimal_zero(service):
    receipt = _create_two_frame_receipt(service)
    first = receipt["frames"][0]
    assert first["interpretation"]["value_type"] == "整数"
    assert first["interpretation"]["parsed_value"] == 0
    assert isinstance(first["interpretation"]["parsed_value"], int)

    r2_view = service._interpret("AA00", next(
        r for r in store.rows("msg_decode") if r["规则编号"] == "R2"
    ))
    assert r2_view["value_type"] == "数字"
    assert r2_view["parsed_value"] == 0.0
    assert isinstance(r2_view["parsed_value"], float)


def test_only_selected_and_reviewed_frames_are_covered(service):
    receipt = _create_two_frame_receipt(service)
    rid = receipt["id"]

    # 未勾选帧不能审阅
    entry, msg = service.review_frame(rid, "F1", "值班员甲")
    assert entry is None and "未勾选" in msg

    # 只勾选第一帧；第二帧保持在草稿中
    entry, msg = service.set_frame_selection(rid, "F1", True)
    assert entry and msg
    assert entry["selected_count"] == 1
    assert entry["unselected_count"] == 1
    assert entry["frames"][1]["selected"] is False

    # 没逐条审阅前不能确认
    entry, msg = service.confirm_receipt(rid, "值班员甲")
    assert entry is None and "尚未逐条审阅" in msg

    # 非操作人不能代为审阅
    entry, msg = service.review_frame(rid, "F1", "值班员乙")
    assert entry is None and "本人" in msg

    # 操作人逐条审阅后确认
    entry, msg = service.review_frame(rid, "F1", "值班员甲")
    assert entry and "已审阅确认" in msg
    entry, msg = service.confirm_receipt(rid, "值班员甲")
    assert entry and entry["status"] == STATUS_CONFIRMED
    assert entry["abnormal"] is False


def test_rule_disabled_after_confirm_conflicts_and_requires_re_review(
    service, decode_service
):
    receipt = _create_two_frame_receipt(service)
    rid = receipt["id"]
    service.set_frame_selection(rid, "F1", True)
    service.review_frame(rid, "F1", "值班员甲")
    entry, _ = service.confirm_receipt(rid, "值班员甲")
    assert entry["status"] == STATUS_CONFIRMED

    # 审核人确认后等待路由答复期间，管理员停用 R1（通过解码模块动作接口）
    r1_id = next(r["id"] for r in store.rows("msg_decode") if r["规则编号"] == "R1")
    rule, message = decode_service.run_action(r1_id, "停用规则")
    assert rule["status"] == "已停用"
    assert "冲突" in message

    # 回单转需要重审，选中帧保留，审阅标记作废
    receipt = store.find("trial_receipt", rid)
    assert receipt["status"] == REVIEW_REQUIRED
    assert receipt["abnormal"] is True
    assert receipt["conflict_rule"] == "R1"
    assert receipt["待办步骤"].startswith("规则 R1 停用冲突")
    assert receipt["frames"][0]["selected"] is True
    assert receipt["frames"][0]["reviewed"] is False

    # 未重审直接发送被拒绝
    entry, msg = service.send_trial(rid, "值班员甲", "token-1")
    assert entry is None and "重审" in msg

    # 仍用已停用的 R1 重审被拒绝
    entry, msg = service.re_review_frame(rid, "F1", "值班员甲")
    assert entry is None and "已停用" in msg

    # 改用 R2：呈现不同解释（数字 0.0 而非整数 0），同一操作人再次确认
    entry, msg = service.re_review_frame(rid, "F1", "值班员甲", new_rule_code="R2")
    assert entry and "重审确认" in msg
    frame = entry["frames"][0]
    assert frame["rule_code"] == "R2"
    assert frame["interpretation"]["value_type"] == "数字"
    assert frame["interpretation"]["parsed_value"] == 0.0
    assert frame["interpretation_changed"] is True
    assert frame["confirmed_by"] == "值班员甲"
    assert entry["status"] == STATUS_CONFIRMED
    assert entry["abnormal"] is False


def test_idempotent_send_produces_single_delivery_matching_final_interpretation(
    service, decode_service
):
    receipt = _create_two_frame_receipt(service)
    rid = receipt["id"]
    service.set_frame_selection(rid, "F1", True)
    service.review_frame(rid, "F1", "值班员甲")
    service.confirm_receipt(rid, "值班员甲")

    r1_id = next(r["id"] for r in store.rows("msg_decode") if r["规则编号"] == "R1")
    decode_service.run_action(r1_id, "停用规则")
    service.re_review_frame(rid, "F1", "值班员甲", new_rule_code="R2")

    # 首次签发
    entry, msg = service.send_trial(rid, "值班员甲", "apply-token-001")
    assert entry and entry["status"] == STATUS_SENT
    delivery_id = entry["delivery_id"]
    assert "TRDL-" in delivery_id

    # 发送期间按钮恢复可点、浏览器用原申请再次确认：同一次投递，不新增
    entry2, msg2 = service.send_trial(rid, "值班员甲", "apply-token-001")
    assert entry2 and entry2["delivery_id"] == delivery_id
    assert "同一次路由投递" in msg2

    # 新申请凭证不能再投递
    entry3, msg3 = service.send_trial(rid, "值班员甲", "apply-token-002")
    assert entry3 is None and "不能用新的申请" in msg3

    # 路由详情只有一笔实际投递
    deliveries = service.list_deliveries(rid)
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery["delivery_id"] == delivery_id
    assert delivery["target_topic"] == "sink/trial/verified"
    assert delivery["router_code"] == "RT-TRIAL-01"
    assert delivery["frame_count"] == 1  # 未勾选的第二帧未投递

    # 投递字段与最终确认（R2）的解释相符
    sent_frame = delivery["frames"][0]
    assert sent_frame["rule_code"] == "R2"
    assert sent_frame["value_type"] == "数字"
    assert sent_frame["parsed_value"] == 0.0
    assert sent_frame["confirmed_by"] == "值班员甲"


def test_disabled_rule_before_confirm_blocks_until_re_review(service, decode_service):
    """准备期间（确认之前）规则停用：确认动作本身触发需要重审。"""
    receipt = _create_two_frame_receipt(service)
    rid = receipt["id"]
    service.set_frame_selection(rid, "F1", True)
    service.review_frame(rid, "F1", "值班员甲")

    r1_id = next(r["id"] for r in store.rows("msg_decode") if r["规则编号"] == "R1")
    decode_service.run_action(r1_id, "停用规则")

    # 停用钩子立即将回单转需要重审并作废审阅，确认被拦下
    receipt = store.find("trial_receipt", rid)
    assert receipt["status"] == REVIEW_REQUIRED
    assert receipt["abnormal"] is True
    assert receipt["frames"][0]["reviewed"] is False
    entry, msg = service.confirm_receipt(rid, "值班员甲")
    assert entry is None and ("重审" in msg or "审阅" in msg)
    assert store.find("trial_receipt", rid)["status"] == REVIEW_REQUIRED

    entry, _ = service.re_review_frame(rid, "F1", "值班员甲", new_rule_code="R2")
    assert entry["status"] == STATUS_CONFIRMED


def test_unselected_frame_remains_in_draft_and_is_not_delivered(service):
    receipt = _create_two_frame_receipt(service)
    rid = receipt["id"]
    service.set_frame_selection(rid, "F1", True)
    service.review_frame(rid, "F1", "值班员甲")
    service.confirm_receipt(rid, "值班员甲")
    entry, _ = service.send_trial(rid, "值班员甲", "token-x")
    assert entry["status"] == STATUS_SENT

    receipt = store.find("trial_receipt", rid)
    assert receipt["frames"][1]["selected"] is False
    deliveries = service.list_deliveries(rid)
    assert {f["frame_id"] for f in deliveries[0]["frames"]} == {"F1"}


def test_receipt_statuses_cover_three_required_states(service):
    receipt = _create_two_frame_receipt(service)
    rid = receipt["id"]
    assert receipt["status"] == STATUS_DRAFT
    service.set_frame_selection(rid, "F1", True)
    service.review_frame(rid, "F1", "值班员甲")
    entry, _ = service.confirm_receipt(rid, "值班员甲")
    assert entry["status"] == STATUS_CONFIRMED
    entry, _ = service.send_trial(rid, "值班员甲", "token-y")
    assert entry["status"] == STATUS_SENT
    # 需要重审作为叠加态常量存在，并通过 abnormal + 待办步骤呈现
    assert REVIEW_REQUIRED == "需要重审"


def test_generic_resource_endpoints_cannot_bypass_workflow(service):
    """通用登记/动作接口不能绕过回单工作流产生无帧记录或非法状态流转。"""
    entry, missing = service.create_entry({"操作人": "值班员甲"})
    assert entry is None and missing
    assert service.run_action(1, "确认试投")[0] is None
    assert store.rows("trial_receipt") == []


def test_sent_receipt_is_locked_against_further_changes(service):
    receipt = _create_two_frame_receipt(service)
    rid = receipt["id"]
    service.set_frame_selection(rid, "F1", True)
    service.review_frame(rid, "F1", "值班员甲")
    service.confirm_receipt(rid, "值班员甲")
    service.send_trial(rid, "值班员甲", "token-lock")

    assert service.set_frame_selection(rid, "F2", True)[0] is None
    assert service.review_frame(rid, "F1", "值班员甲")[0] is None
    assert service.re_review_frame(rid, "F1", "值班员甲", "R2")[0] is None
    assert service.confirm_receipt(rid, "值班员甲")[0] is None


def test_failed_dispatch_allows_safe_retry_with_same_token(service, monkeypatch):
    receipt = _create_two_frame_receipt(service)
    rid = receipt["id"]
    service.set_frame_selection(rid, "F1", True)
    service.review_frame(rid, "F1", "值班员甲")
    service.confirm_receipt(rid, "值班员甲")

    calls = {"count": 0}

    def boom(_receipt, _frames):
        calls["count"] += 1
        raise RuntimeError("路由暂不可用")

    monkeypatch.setattr(service, "_dispatch", boom)
    with pytest.raises(RuntimeError):
        service.send_trial(rid, "值班员甲", "token-retry")

    receipt = store.find("trial_receipt", rid)
    assert receipt["pending_delivery"] is False
    assert receipt["status"] != STATUS_SENT
    assert service.list_deliveries(rid) == []

    # 恢复后用同一申请凭证重试，只产生一笔投递
    monkeypatch.undo()
    entry, message = service.send_trial(rid, "值班员甲", "token-retry")
    assert entry["status"] == STATUS_SENT and "TRDL-" in message
    assert len(service.list_deliveries(rid)) == 1
