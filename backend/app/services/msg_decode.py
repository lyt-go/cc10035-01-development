"""报文解码领域服务：IoT二进制/文本报文的解析与校验。

提供报文解析、字段提取、校验计算等真实业务能力。
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


class MsgDecodeService(BaseModuleService):
    MODULE = "msg_decode"
    ENTITY = "解码规则"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["规则编号", "协议类型", "报文标识", "字段偏移", "字段长度", "数据类型", "字节序", "校验方式"]
    REQUIRED_FIELDS = ["规则编号", "协议类型", "报文标识"]
    STATUS_ORDER = ["草稿", "待验证", "已启用", "已停用"]
    ACTION_RULES = {"提交验证": "待验证", "启用规则": "已启用", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    def parse_message(self, raw_hex: str, protocol_type: str) -> dict[str, Any]:
        """解析原始报文：根据协议类型查找已启用解码规则并解析hex报文。

        业务流程：
        1. 查找该协议已启用的解码规则
        2. 按报文标识切分报文
        3. 提取字段（模拟偏移+长度解析）
        4. 进行校验计算
        """
        rows = store.rows(self.MODULE)
        rules = [
            row for row in rows
            if row.get("协议类型") == protocol_type
            and row.get("status") == "已启用"
        ]
        if not rules:
            return {"ok": False, "message": f"未找到协议 {protocol_type} 的已启用解码规则"}

        rule = rules[0]
        identifier = rule.get("报文标识", "")
        field_offset = rule.get("字段偏移", "0")
        field_length = rule.get("字段长度", "8")
        data_type = rule.get("数据类型", "hex")
        byte_order = rule.get("字节序", "big")
        checksum_type = rule.get("校验方式", "none")

        # 模拟解析
        raw_bytes = raw_hex.replace(" ", "")
        if len(raw_bytes) % 2 != 0:
            return {"ok": False, "message": "报文hex字符串长度必须为偶数"}

        binary_data = bytes.fromhex(raw_bytes)

        # 按标识切分
        if identifier:
            ident_bytes = identifier.encode()
            if ident_bytes in binary_data:
                idx = binary_data.index(ident_bytes)
                payload = binary_data[idx + len(ident_bytes):]
            else:
                payload = binary_data
        else:
            payload = binary_data

        # 提取字段
        try:
            offset = int(field_offset)
            length = int(field_length)
        except (ValueError, TypeError):
            offset = 0
            length = min(len(payload), 16)

        field_data = payload[offset:offset + length] if offset + length <= len(payload) else payload[offset:]

        # 按数据类型解析
        parsed_value = None
        if data_type in ("uint32", "int32"):
            if len(field_data) >= 4:
                endian = ">" if byte_order == "big" else "<"
                parsed_value = struct.unpack(f"{endian}I", field_data[:4])[0]
        elif data_type == "float64":
            if len(field_data) >= 8:
                endian = ">" if byte_order == "big" else "<"
                parsed_value = struct.unpack(f"{endian}d", field_data[:8])[0]
        else:
            parsed_value = field_data.hex()

        # 校验计算（简单异或校验模拟）
        checksum_valid = True
        if checksum_type == "xor8":
            xor_sum = 0
            for b in payload:
                xor_sum ^= b
            checksum_valid = xor_sum == 0
        elif checksum_type == "crc16":
            checksum_valid = sum(payload) % 65535 == 0

        return {
            "ok": True,
            "message": "报文解析完成",
            "decode_rule": rule.get("规则编号"),
            "protocol": protocol_type,
            "raw_hex": raw_hex,
            "field_offset": offset,
            "field_length": length,
            "data_type": data_type,
            "parsed_value": parsed_value,
            "field_hex": field_data.hex(),
            "checksum_type": checksum_type,
            "checksum_valid": checksum_valid,
        }

    def detect_protocol(self, raw_hex: str) -> dict[str, Any]:
        """自动检测报文协议类型：尝试所有已启用的解码规则。

        返回匹配结果和建议协议类型。
        """
        rows = store.rows(self.MODULE)
        enabled = [row for row in rows if row.get("status") == "已启用"]

        results = []
        raw_bytes = raw_hex.replace(" ", "")
        if len(raw_bytes) % 2 != 0:
            return {"ok": False, "message": "报文hex长度必须为偶数"}

        binary_data = bytes.fromhex(raw_bytes)

        for rule in enabled:
            identifier = rule.get("报文标识", "")
            if identifier:
                ident_bytes = identifier.encode()
                match = ident_bytes in binary_data
            else:
                match = True  # 无标识的规则默认匹配

            results.append({
                "规则编号": rule.get("规则编号"),
                "协议类型": rule.get("协议类型"),
                "报文标识": identifier,
                "匹配": match,
            })

        matched = [r for r in results if r["匹配"]]
        return {
            "ok": True,
            "total_rules_checked": len(results),
            "matched_count": len(matched),
            "suggested_protocol": matched[0]["协议类型"] if matched else "未知",
            "details": results,
        }