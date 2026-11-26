"""试投回单领域服务：值班员向消息路由签发试投的回单工作流。

业务流程：
1. 录入原始帧与拟用解码规则，生成草稿回单；
2. 勾选准备试投的帧并逐条审阅字段解释（一条回单只覆盖勾选帧，未勾选留在草稿）；
3. 全部勾选帧审阅后才可确认，回单进入「已确认可投」；
4. 准备期间若规则被停用，与这笔尚未发送的试投冲突，回单转「需要重审」；
5. 重审时若改用其他规则（如 R1→R2），页面呈现不同解释并由同一操作人再次确认；
6. 签发试投按申请幂等：同一申请（client_token）即使重复提交也只产生一次路由投递；
7. 实际投递写入路由投递记录表，回单转「已发送」，可凭投递编号追到实际目的地。
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

CST = timezone(timedelta(hours=8))

# 回单状态：草稿 → 已确认可投 → 已发送；规则停用冲突时进入需要重审，重审后恢复可投。
STATUS_DRAFT = "草稿"
STATUS_CONFIRMED = "已确认可投"
STATUS_SENT = "已发送"
REVIEW_REQUIRED = "需要重审"


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _find_rule(rule_code: str) -> dict[str, Any] | None:
    for row in store.rows("msg_decode"):
        if row.get("规则编号") == rule_code:
            return row
    return None


class TrialReceiptService(BaseModuleService):
    MODULE = "trial_receipt"
    ENTITY = "试投回单"
    OPERATION_TERM = "试投"
    LIST_FIELDS = ["回单编号", "操作人", "创建时间", "选中帧数", "未勾选帧数", "待办步骤"]
    REQUIRED_FIELDS = ["操作人"]
    STATUS_ORDER = [STATUS_DRAFT, STATUS_CONFIRMED, STATUS_SENT]
    ACTION_RULES = {
        "确认试投": STATUS_CONFIRMED,
        "重新确认": STATUS_CONFIRMED,
        "签发试投": STATUS_SENT,
    }
    NEGATIVE_ACTIONS: list[str] = []

    # ------------------------------------------------------------------
    # 回单与帧管理
    # ------------------------------------------------------------------
    def create_receipt(
        self,
        operator: str,
        frames: list[dict[str, str]],
        source_topic: str = "trial/device/dev-01/temp",
    ) -> tuple[dict[str, Any] | None, str]:
        """创建草稿回单：录入原始帧与拟用解码规则，并生成逐条字段解释。"""
        operator = (operator or "").strip()
        if not operator:
            return None, "缺少操作人"
        if not frames:
            return None, "至少录入一帧原始报文"

        rows = store.rows(self.MODULE)
        receipt_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        receipt_frames: list[dict[str, Any]] = []
        for index, item in enumerate(frames, start=1):
            raw_hex = (item.get("raw_hex") or "").strip()
            rule_code = (item.get("rule_code") or "").strip()
            if not raw_hex:
                return None, f"第 {index} 帧缺少原始帧内容"
            if not rule_code:
                return None, f"第 {index} 帧缺少拟用解码规则"
            rule = _find_rule(rule_code)
            if rule is None:
                return None, f"解码规则 {rule_code} 不存在"
            interpretation = self._interpret(raw_hex, rule)
            if not interpretation["ok"]:
                return None, f"第 {index} 帧无法解释：{interpretation['message']}"
            receipt_frames.append({
                "frame_id": f"F{index}",
                "raw_hex": raw_hex,
                "rule_code": rule_code,
                "selected": False,          # 默认未勾选，保留在草稿中
                "reviewed": False,          # 字段解释尚未逐条审阅
                "interpretation": interpretation,
                "confirmed_by": None,       # 逐条审阅确认人
                "reviewed_at": None,
            })

        receipt = {
            "id": receipt_id,
            "回单编号": f"TRCP-{receipt_id:04d}",
            "操作人": operator,
            "创建时间": _now(),
            "source_topic": source_topic,
            "status": STATUS_DRAFT,
            "pending": True,
            "abnormal": False,
            "frames": receipt_frames,
            "selected_count": 0,
            "unselected_count": len(receipt_frames),
            "待办步骤": "勾选帧并逐条审阅字段解释",
            "conflict_rule": None,          # 触发重审的停用规则
            "confirmed_at": None,
            "sent_at": None,
            "delivery_id": None,
            "client_token": None,
        }
        rows.append(receipt)
        return receipt, "试投回单草稿已创建"

    def set_frame_selection(
        self, receipt_id: int, frame_id: str, selected: bool
    ) -> tuple[dict[str, Any] | None, str]:
        """勾选/取消勾选帧。一张回单只覆盖勾选帧，未勾选项保持在草稿中。"""
        receipt = store.find(self.MODULE, receipt_id)
        if receipt is None:
            return None, f"试投回单 {receipt_id} 不存在或已归档"
        if receipt["status"] == STATUS_SENT:
            return None, "回单已发送，帧选择不可再变更"
        frame = self._get_frame(receipt, frame_id)
        if frame is None:
            return None, f"帧 {frame_id} 不在该回单内"
        frame["selected"] = bool(selected)
        if not selected:
            # 取消勾选后审阅记录一并作废，需要重新逐条审阅。
            frame["reviewed"] = False
            frame["confirmed_by"] = None
            frame["reviewed_at"] = None
        self._refresh(receipt)
        return receipt, f"帧 {frame_id} 已{'勾选' if selected else '取消勾选'}"

    def review_frame(
        self, receipt_id: int, frame_id: str, operator: str
    ) -> tuple[dict[str, Any] | None, str]:
        """逐条审阅字段解释：只有勾选帧且看过字段解释后才能审阅确认。"""
        receipt = store.find(self.MODULE, receipt_id)
        if receipt is None:
            return None, f"试投回单 {receipt_id} 不存在或已归档"
        if receipt["status"] == STATUS_SENT:
            return None, "回单已发送，解释不可再审阅"
        frame = self._get_frame(receipt, frame_id)
        if frame is None:
            return None, f"帧 {frame_id} 不在该回单内"
        if not frame["selected"]:
            return None, f"帧 {frame_id} 未勾选，不能纳入本回单审阅"
        if (operator or "").strip() != receipt["操作人"]:
            return None, "字段解释必须由本回单操作人本人逐条确认"
        # 规则在审阅期间被停用：解释已失效，必须重审。
        rule = _find_rule(frame["rule_code"])
        if rule is None or rule.get("status") == "已停用":
            self._mark_review_required(receipt, frame["rule_code"])
            return None, f"规则 {frame['rule_code']} 已停用，解释失效，请重审"
        frame["reviewed"] = True
        frame["confirmed_by"] = operator.strip()
        frame["reviewed_at"] = _now()
        self._refresh(receipt)
        return receipt, f"帧 {frame_id} 的字段解释已审阅确认"

    def confirm_receipt(
        self, receipt_id: int, operator: str
    ) -> tuple[dict[str, Any] | None, str]:
        """确认试投：所有勾选帧均逐条审阅后才可进入「已确认可投」。"""
        receipt = store.find(self.MODULE, receipt_id)
        if receipt is None:
            return None, f"试投回单 {receipt_id} 不存在或已归档"
        if receipt["status"] == STATUS_SENT:
            return None, "回单已发送，无需重复确认"
        if (operator or "").strip() != receipt["操作人"]:
            return None, "试投确认必须由本回单操作人本人完成"
        selected = [f for f in receipt["frames"] if f["selected"]]
        if not selected:
            return None, "请先勾选至少一帧再确认试投"
        unreviewed = [f["frame_id"] for f in selected if not f["reviewed"]]
        if unreviewed:
            return None, f"以下帧尚未逐条审阅字段解释：{'、'.join(unreviewed)}"
        # 确认前再校验一次规则状态，防止准备期间规则被停用。
        disabled = self._disabled_rules(receipt)
        if disabled:
            self._mark_review_required(receipt, disabled[0])
            return None, f"规则 {disabled[0]} 已停用，与本笔试投冲突，请重审"
        receipt["status"] = STATUS_CONFIRMED
        receipt["abnormal"] = False
        receipt["conflict_rule"] = None
        receipt["confirmed_at"] = _now()
        receipt["pending"] = True
        receipt["待办步骤"] = "等待路由答复，可签发试投"
        return receipt, "试投回单已确认可投，等待路由答复"

    def re_review_frame(
        self,
        receipt_id: int,
        frame_id: str,
        operator: str,
        new_rule_code: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """重审：规则停用后改用新规则（如 R1→R2），呈现不同解释并再次确认。"""
        receipt = store.find(self.MODULE, receipt_id)
        if receipt is None:
            return None, f"试投回单 {receipt_id} 不存在或已归档"
        if receipt["status"] == STATUS_SENT:
            return None, "回单已发送，解释不可再重审"
        if (operator or "").strip() != receipt["操作人"]:
            return None, "重审确认必须由本回单操作人本人完成"
        frame = self._get_frame(receipt, frame_id)
        if frame is None:
            return None, f"帧 {frame_id} 不在该回单内"

        if new_rule_code and new_rule_code != frame["rule_code"]:
            rule = _find_rule(new_rule_code)
            if rule is None:
                return None, f"解码规则 {new_rule_code} 不存在"
            if rule.get("status") == "已停用":
                return None, f"规则 {new_rule_code} 已停用，不能用于重审"
            old_interpretation = frame["interpretation"]
            new_interpretation = self._interpret(frame["raw_hex"], rule)
            if not new_interpretation["ok"]:
                return None, f"新规则解释失败：{new_interpretation['message']}"
            frame["rule_code"] = new_rule_code
            frame["interpretation"] = new_interpretation
            frame["interpretation_changed"] = (
                old_interpretation["value_type"] != new_interpretation["value_type"]
                or old_interpretation["parsed_value"] != new_interpretation["parsed_value"]
            )
        else:
            rule = _find_rule(frame["rule_code"])
            if rule is None or rule.get("status") == "已停用":
                return None, f"规则 {frame['rule_code']} 已停用，请改用其他规则重审"

        frame["reviewed"] = True
        frame["confirmed_by"] = operator.strip()
        frame["reviewed_at"] = _now()
        self._refresh(receipt)
        # 所有勾选帧重审完成且无停用规则后，回单回到已确认可投。
        selected = [f for f in receipt["frames"] if f["selected"]]
        if selected and all(f["reviewed"] for f in selected) and not self._disabled_rules(receipt):
            receipt["status"] = STATUS_CONFIRMED
            receipt["abnormal"] = False
            receipt["conflict_rule"] = None
            receipt["confirmed_at"] = _now()
            receipt["待办步骤"] = "重审完成，可再次签发试投"
        return receipt, f"帧 {frame_id} 已按规则 {frame['rule_code']} 重审确认"

    def send_trial(
        self, receipt_id: int, operator: str, client_token: str
    ) -> tuple[dict[str, Any] | None, str]:
        """签发试投：按申请幂等，同一 client_token 重复提交只产生一次路由投递。"""
        receipt = store.find(self.MODULE, receipt_id)
        if receipt is None:
            return None, f"试投回单 {receipt_id} 不存在或已归档"
        token = (client_token or "").strip()
        if not token:
            return None, "缺少试投申请凭证 client_token"
        if (operator or "").strip() != receipt["操作人"]:
            return None, "试投签发必须由本回单操作人本人完成"

        # 幂等：发送期间按钮恢复可点、浏览器用原申请再次确认，只能拿到同一次投递。
        if receipt["status"] == STATUS_SENT:
            if receipt.get("client_token") == token:
                return receipt, "该试投申请已受理，返回同一次路由投递"
            return None, "回单已发送，不能用新的申请重复投递"
        if receipt.get("client_token") and receipt["client_token"] != token:
            return None, "该回单存在进行中的试投申请，请用原申请凭证确认"
        if receipt["client_token"] == token and receipt.get("pending_delivery"):
            # 同一申请在途：直接返回，不产生第二笔投递。
            return receipt, "试投申请处理中，已绑定同一次路由投递"

        # 发送前最终防线：规则停用冲突未重审，禁止发送。
        disabled = self._disabled_rules(receipt)
        if disabled:
            self._mark_review_required(receipt, disabled[0])
            return None, f"规则 {disabled[0]} 已停用，最终发送的解释须经操作人重审确认"
        selected = [f for f in receipt["frames"] if f["selected"]]
        if not selected:
            return None, "没有勾选帧，无法签发试投"
        if any(not f["reviewed"] for f in selected):
            return None, "存在未逐条审阅的勾选帧，无法签发试投"

        receipt["client_token"] = token
        receipt["pending_delivery"] = True
        receipt["待办步骤"] = "路由投递处理中，等待答复"

        try:
            delivery = self._dispatch(receipt, selected)
            store.rows("trial_delivery").append(delivery)
        except Exception:
            # 投递未落地：清除在途标记，允许用同一申请凭证安全重试（不会产生重复投递）。
            receipt["pending_delivery"] = False
            raise

        receipt["status"] = STATUS_SENT
        receipt["pending"] = False
        receipt["abnormal"] = False
        receipt["sent_at"] = delivery["delivered_at"]
        receipt["delivery_id"] = delivery["delivery_id"]
        receipt["pending_delivery"] = False
        receipt["待办步骤"] = "已完成，路由答复已归档"
        return receipt, f"试投已签发，路由投递编号 {delivery['delivery_id']}"

    def list_deliveries(self, receipt_id: int | None = None) -> list[dict[str, Any]]:
        """路由详情：回单对应的实际投递，字段与最终确认的解释相符。"""
        rows = store.rows("trial_delivery")
        if receipt_id is None:
            return rows
        return [row for row in rows if row["receipt_id"] == receipt_id]

    # ------------------------------------------------------------------
    # 冲突钩子：报文解码模块停用规则时调用
    # ------------------------------------------------------------------
    def on_rule_disabled(self, rule_code: str) -> list[dict[str, Any]]:
        """规则停用与尚未发送的试投冲突：相关回单转「需要重审」。"""
        affected: list[dict[str, Any]] = []
        for receipt in store.rows(self.MODULE):
            if receipt["status"] == STATUS_SENT:
                continue
            uses_rule = any(
                f["selected"] and f["rule_code"] == rule_code
                for f in receipt["frames"]
            )
            if uses_rule:
                self._mark_review_required(receipt, rule_code)
                affected.append({
                    "receipt_id": receipt["id"],
                    "回单编号": receipt["回单编号"],
                    "conflict_rule": rule_code,
                })
        return affected

    # ------------------------------------------------------------------
    # 解释引擎与内部工具
    # ------------------------------------------------------------------
    def _interpret(self, raw_hex: str, rule: dict[str, Any]) -> dict[str, Any]:
        """按解码规则解释原始帧，输出带类型的字段解释（R1→整数0，R2→数字0.0）。"""
        clean = raw_hex.replace(" ", "")
        if len(clean) % 2 != 0:
            return {"ok": False, "message": "报文 hex 长度必须为偶数"}
        try:
            binary = bytes.fromhex(clean)
        except ValueError:
            return {"ok": False, "message": "报文含非法 hex 字符"}

        identifier = rule.get("报文标识", "") or ""
        payload = binary
        if identifier:
            ident = bytes.fromhex(identifier) if all(
                ch in "0123456789abcdefABCDEF" for ch in identifier
            ) else identifier.encode()
            idx = binary.find(ident)
            if idx >= 0:
                payload = binary[idx + len(ident):]

        try:
            offset = int(rule.get("字段偏移", 0))
            length = int(rule.get("字段长度", 1))
        except (ValueError, TypeError):
            offset, length = 0, 1
        field_data = payload[offset:offset + length] or payload[:1]
        data_type = rule.get("数据类型", "hex")
        big_endian = rule.get("字节序", "big") == "big"
        endian = ">" if big_endian else "<"

        if data_type in ("int32", "uint32"):
            padded = field_data[:4].ljust(4, b"\x00")
            number = struct.unpack(f"{endian}I", padded)[0]
            parsed_value: Any = int(number)
            value_type = "整数"
        elif data_type == "float64":
            padded = field_data[:8].ljust(8, b"\x00")
            parsed_value = round(float(struct.unpack(f"{endian}d", padded)[0]), 4)
            value_type = "数字"
        else:
            parsed_value = field_data.hex()
            value_type = "hex字符串"

        return {
            "ok": True,
            "rule_code": rule.get("规则编号"),
            "rule_status": rule.get("status"),
            "data_type": data_type,
            "value_type": value_type,
            "parsed_value": parsed_value,
            "field_hex": field_data.hex(),
            "field_offset": offset,
            "field_length": length,
            "byte_order": rule.get("字节序", "big"),
            "explanation": (
                f"按规则 {rule.get('规则编号')}（{data_type}）解释字段 "
                f"{field_data.hex()}，得到{value_type} {parsed_value!r}"
            ),
        }

    def _dispatch(
        self, receipt: dict[str, Any], frames: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """实际路由投递：匹配消息路由规则，记录可追到目的地的投递详情。"""
        source_topic = receipt.get("source_topic", "trial/device/dev-01/temp")
        target_topic = "sink/trial/default"
        router_code = None
        forward_method = "MQTT"
        for router in store.rows("msg_router"):
            if router.get("status") != "已启用":
                continue
            if self._topic_match(source_topic, router.get("匹配Topic", "")):
                target_topic = router.get("目标Topic", target_topic)
                router_code = router.get("规则编号")
                forward_method = router.get("转发方式", forward_method)
                break

        delivery_id = (
            max((int(d.get("id", 0)) for d in store.rows("trial_delivery")), default=0) + 1
        )
        # 投递载荷逐帧固化最终确认的解释，保证路由详情与回单解释相符。
        payload_frames = [
            {
                "frame_id": f["frame_id"],
                "raw_hex": f["raw_hex"],
                "rule_code": f["rule_code"],
                "value_type": f["interpretation"]["value_type"],
                "parsed_value": f["interpretation"]["parsed_value"],
                "confirmed_by": f["confirmed_by"],
            }
            for f in frames
        ]
        return {
            "id": delivery_id,
            "delivery_id": f"TRDL-{delivery_id:04d}",
            "receipt_id": receipt["id"],
            "回单编号": receipt["回单编号"],
            "client_token": receipt["client_token"],
            "source_topic": source_topic,
            "router_code": router_code,
            "target_topic": target_topic,
            "forward_method": forward_method,
            "frames": payload_frames,
            "frame_count": len(payload_frames),
            "delivered_at": _now(),
        }

    @staticmethod
    def _topic_match(source: str, pattern: str) -> bool:
        source_parts = source.split("/")
        pattern_parts = pattern.split("/")
        for i, part in enumerate(pattern_parts):
            if part == "#":
                return i <= len(source_parts)
            if i >= len(source_parts):
                return False
            if part == "+":
                continue
            if part != source_parts[i]:
                return False
        return len(source_parts) == len(pattern_parts)

    @staticmethod
    def _get_frame(receipt: dict[str, Any], frame_id: str) -> dict[str, Any] | None:
        for frame in receipt["frames"]:
            if frame["frame_id"] == frame_id:
                return frame
        return None

    def _disabled_rules(self, receipt: dict[str, Any]) -> list[str]:
        disabled: list[str] = []
        for frame in receipt["frames"]:
            if not frame["selected"]:
                continue
            rule = _find_rule(frame["rule_code"])
            if rule is None or rule.get("status") == "已停用":
                if frame["rule_code"] not in disabled:
                    disabled.append(frame["rule_code"])
        return disabled

    def _mark_review_required(
        self, receipt: dict[str, Any], rule_code: str
    ) -> None:
        """转「需要重审」：保留选中帧，仅把受影响解释的审阅标记作废。"""
        receipt["abnormal"] = True
        receipt["status"] = REVIEW_REQUIRED
        receipt["conflict_rule"] = rule_code
        receipt["待办步骤"] = f"规则 {rule_code} 停用冲突，需重审并再次确认解释"
        for frame in receipt["frames"]:
            if frame["selected"] and frame["rule_code"] == rule_code:
                frame["reviewed"] = False
                frame["confirmed_by"] = None
                frame["reviewed_at"] = None
                frame["interpretation_changed"] = False

    def _refresh(self, receipt: dict[str, Any]) -> None:
        selected = [f for f in receipt["frames"] if f["selected"]]
        receipt["selected_count"] = len(selected)
        receipt["unselected_count"] = len(receipt["frames"]) - len(selected)
        if receipt["status"] == STATUS_SENT:
            return
        # 冲突帧已被取消勾选时，重审态随之解除。
        if receipt["abnormal"] and not self._disabled_rules(receipt):
            receipt["abnormal"] = False
            receipt["conflict_rule"] = None
            if receipt["status"] == REVIEW_REQUIRED:
                receipt["status"] = STATUS_DRAFT
        if receipt["abnormal"]:
            return
        if not selected:
            receipt["status"] = STATUS_DRAFT
            receipt["待办步骤"] = "勾选帧并逐条审阅字段解释"
        elif any(not f["reviewed"] for f in selected):
            receipt["status"] = STATUS_DRAFT
            receipt["待办步骤"] = "逐条审阅勾选帧的字段解释"
        else:
            receipt["status"] = STATUS_CONFIRMED
            receipt["待办步骤"] = "可确认试投并等待路由答复"

    # 通用动作接口走自定义工作流，避免基类状态机绕过校验。
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        return None, "试投回单请使用专用工作流接口（审阅/确认/重审/签发）"

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        # 回单必须带原始帧与解码规则并生成字段解释，禁止走通用登记接口产生无帧记录。
        return None, ["试投回单请使用 POST /api/trial-receipt 录入原始帧后创建"]


trial_receipt_service = TrialReceiptService()
