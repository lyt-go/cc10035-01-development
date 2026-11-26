"""试投回单工作流接口：草稿帧管理、逐条审阅、确认/重审与幂等签发。

与通用资源路由的区别：回单内含帧与解释的嵌套结构，状态流转有额外业务校验，
因此状态机只放在 trial_receipt 领域服务里，路由层不做业务判断。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services.trial_receipt import trial_receipt_service

router = APIRouter(prefix="/api/trial-receipt", tags=["试投回单"])


class FrameInput(BaseModel):
    raw_hex: str
    rule_code: str


class CreateReceiptPayload(BaseModel):
    operator: str
    source_topic: str = "trial/device/dev-01/temp"
    frames: list[FrameInput] = Field(default_factory=list)


class FrameSelectionPayload(BaseModel):
    frame_id: str
    selected: bool


class ReviewPayload(BaseModel):
    frame_id: str
    operator: str


class ReReviewPayload(BaseModel):
    frame_id: str
    operator: str
    new_rule_code: str | None = None


class ConfirmPayload(BaseModel):
    operator: str


class SendPayload(BaseModel):
    operator: str
    client_token: str


def _result(ok: bool, message: str, entry: dict[str, Any] | None = None) -> ActionResult:
    return ActionResult(ok=ok, message=message, entry=entry)


@router.get("/rules")
def available_rules() -> dict[str, Any]:
    """录入页可选解码规则：展示规则编号与当前启停状态。"""
    from app.store import store

    rules = [
        {
            "rule_code": row.get("规则编号"),
            "protocol_type": row.get("协议类型"),
            "data_type": row.get("数据类型"),
            "status": row.get("status"),
        }
        for row in store.rows("msg_decode")
    ]
    return {"items": rules}


@router.post("", response_model=ActionResult)
def create_receipt(payload: CreateReceiptPayload) -> ActionResult:
    frames = [item.model_dump() for item in payload.frames]
    entry, message = trial_receipt_service.create_receipt(
        payload.operator, frames, payload.source_topic
    )
    return _result(entry is not None, message, entry)


@router.post("/{receipt_id}/frames/selection", response_model=ActionResult)
def set_frame_selection(receipt_id: int, payload: FrameSelectionPayload) -> ActionResult:
    entry, message = trial_receipt_service.set_frame_selection(
        receipt_id, payload.frame_id, payload.selected
    )
    return _result(entry is not None, message, entry)


@router.post("/{receipt_id}/frames/review", response_model=ActionResult)
def review_frame(receipt_id: int, payload: ReviewPayload) -> ActionResult:
    entry, message = trial_receipt_service.review_frame(
        receipt_id, payload.frame_id, payload.operator
    )
    return _result(entry is not None, message, entry)


@router.post("/{receipt_id}/frames/re-review", response_model=ActionResult)
def re_review_frame(receipt_id: int, payload: ReReviewPayload) -> ActionResult:
    entry, message = trial_receipt_service.re_review_frame(
        receipt_id, payload.frame_id, payload.operator, payload.new_rule_code
    )
    return _result(entry is not None, message, entry)


@router.post("/{receipt_id}/confirm", response_model=ActionResult)
def confirm_receipt(receipt_id: int, payload: ConfirmPayload) -> ActionResult:
    entry, message = trial_receipt_service.confirm_receipt(receipt_id, payload.operator)
    return _result(entry is not None, message, entry)


@router.post("/{receipt_id}/send", response_model=ActionResult)
def send_trial(receipt_id: int, payload: SendPayload) -> ActionResult:
    entry, message = trial_receipt_service.send_trial(
        receipt_id, payload.operator, payload.client_token
    )
    return _result(entry is not None, message, entry)


@router.get("/{receipt_id}/deliveries")
def list_deliveries(receipt_id: int) -> dict[str, Any]:
    """路由详情：该回单实际产生的投递（幂等保证至多一笔）。"""
    items = trial_receipt_service.list_deliveries(receipt_id)
    return {"items": items, "total": len(items)}
