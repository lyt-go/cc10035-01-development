"""协议配置领域服务：IoT协议定义与生命周期管理。

提供协议版本管理、校验、设备协议统计等真实业务能力。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


class ProtocolCfgService(BaseModuleService):
    MODULE = "protocol_cfg"
    ENTITY = "协议配置"
    OPERATION_TERM = "接入"
    LIST_FIELDS = ["配置编号", "协议名称", "传输方式", "数据格式", "报文分隔符", "心跳间隔", "超时秒数", "配置版本"]
    REQUIRED_FIELDS = ["配置编号", "协议名称", "传输方式"]
    STATUS_ORDER = ["草稿", "待验证", "已生效", "已废弃"]
    ACTION_RULES = {"提交验证": "待验证", "标记生效": "已生效", "标记废弃": "已废弃"}
    NEGATIVE_ACTIONS = []

    def version_upgrade(self, config_id: str, new_version: str) -> dict[str, Any]:
        """协议版本升级：创建新版本并标记旧版本为废弃。

        业务流程：
        1. 查找当前生效的协议配置
        2. 将当前版本标记为废弃
        3. 创建新版本协议
        4. 保留数据格式和传输方式等核心字段
        """
        rows = store.rows(self.MODULE)
        target = None
        for row in rows:
            if row.get("配置编号") == config_id and row.get("status") == "已生效":
                target = row
                break
        if target is None:
            return {"ok": False, "message": f"协议配置 {config_id} 不存在或未生效"}

        # 标记旧版本为废弃
        target["status"] = "已废弃"
        target["pending"] = False

        # 创建新版本
        max_id = max((int(row.get("id", 0)) for row in rows), default=0)
        new_entry = {
            "id": max_id + 1,
            "配置编号": f"{config_id}-v2",
            "协议名称": target.get("协议名称"),
            "传输方式": target.get("传输方式"),
            "数据格式": target.get("数据格式"),
            "报文分隔符": target.get("报文分隔符"),
            "心跳间隔": target.get("心跳间隔"),
            "超时秒数": target.get("超时秒数"),
            "配置版本": new_version,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
        }
        rows.append(new_entry)

        return {
            "ok": True,
            "message": f"协议 {config_id} 已废弃，新版本 {new_version} 已创建",
            "old_version": config_id,
            "new_entry": new_entry,
        }

    def validate_protocol(self, config_id: str) -> dict[str, Any]:
        """协议校验：检查心跳间隔和超时参数是否合理。

        校验规则：
        - 心跳间隔 > 0
        - 超时秒数 > 心跳间隔（确保超时检测合理）
        - 报文分隔符非空
        """
        rows = store.rows(self.MODULE)
        target = None
        for row in rows:
            if row.get("配置编号") == config_id:
                target = row
                break
        if target is None:
            return {"ok": False, "message": f"协议配置 {config_id} 不存在"}

        errors = []
        try:
            heartbeat = float(target.get("心跳间隔", "0"))
        except (ValueError, TypeError):
            heartbeat = 0

        try:
            timeout = float(target.get("超时秒数", "0"))
        except (ValueError, TypeError):
            timeout = 0

        if heartbeat <= 0:
            errors.append("心跳间隔必须大于0")
        if timeout <= heartbeat:
            errors.append(f"超时秒数({timeout})应大于心跳间隔({heartbeat})")
        if not target.get("报文分隔符", "").strip():
            errors.append("报文分隔符不能为空")

        if errors:
            return {"ok": False, "message": "; ".join(errors), "errors": errors}

        return {
            "ok": True,
            "message": f"协议 {config_id} 校验通过",
            "heartbeat_interval": heartbeat,
            "timeout": timeout,
        }

    def device_protocol_distribution(self) -> list[dict[str, Any]]:
        """统计每种协议类型被多少设备使用。"""
        device_rows = store.rows("device_registry")
        dist = {}
        for dev in device_rows:
            proto = dev.get("协议类型", "未知")
            dist[proto] = dist.get(proto, 0) + 1

        return sorted(
            [{"协议类型": k, "设备数": v} for k, v in dist.items()],
            key=lambda x: x["设备数"], reverse=True,
        )