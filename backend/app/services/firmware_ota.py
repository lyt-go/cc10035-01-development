"""固件升级领域服务：IoT设备固件OTA升级任务管理。

提供升级任务创建、进度追踪、回滚、兼容性校验、批量升级等真实业务能力。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _now_dt() -> datetime:
    return datetime.now(CST)


class FirmwareOtaService(BaseModuleService):
    MODULE = "firmware_ota"
    ENTITY = "升级任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["任务编号", "目标设备", "固件版本", "升级包摘要", "升级策略", "目标数量", "成功数量", "任务进度"]
    REQUIRED_FIELDS = ["任务编号", "目标设备", "固件版本"]
    STATUS_ORDER = ["待执行", "升级中", "已完成", "部分失败"]
    ACTION_RULES = {"发起升级": "升级中", "标记完成": "已完成", "标记失败": "部分失败"}
    NEGATIVE_ACTIONS = []

    def create_ota_task(self, target_device: str, firmware_version: str,
                        upgrade_strategy: str = "rolling",
                        target_count: int = 10) -> dict[str, Any]:
        """创建固件升级任务。

        业务流程：
        1. 校验设备是否存在
        2. 检查是否有进行中的升级任务
        3. 校验固件版本格式
        4. 创建升级任务
        """
        device_rows = store.rows("device_registry")
        device = None
        for row in device_rows:
            if row.get("设备编号") == target_device:
                device = row
                break
        if device is None:
            return {"ok": False, "message": f"设备 {target_device} 未注册"}

        # 检查进行中的任务
        rows = store.rows(self.MODULE)
        for row in rows:
            if (row.get("目标设备") == target_device
                    and row.get("status") in ("待执行", "升级中")):
                return {
                    "ok": False,
                    "message": f"设备 {target_device} 已有进行中的升级任务({row.get('任务编号')})",
                }

        # 版本格式校验
        if not firmware_version.startswith("v"):
            return {"ok": False, "message": "固件版本必须以v开头，如v2.0.1"}

        max_id = max((int(row.get("id", 0)) for row in rows), default=0)
        task = {
            "id": max_id + 1,
            "任务编号": f"OTA-{max_id + 1:04d}",
            "目标设备": target_device,
            "固件版本": firmware_version,
            "升级包摘要": f"sha256:mock_{firmware_version}_{_now_dt().timestamp():.0f}",
            "升级策略": upgrade_strategy,
            "目标数量": target_count,
            "成功数量": 0,
            "任务进度": "0%",
            "status": "待执行",
            "pending": True,
            "abnormal": False,
        }
        rows.append(task)

        # 更新设备固件版本记录
        device["固件版本"] = f"{device.get('固件版本', 'v1.0.0')}→{firmware_version}"

        return {
            "ok": True,
            "message": f"固件升级任务 {task['任务编号']} 已创建",
            "task": task,
        }

    def update_progress(self, task_id: str, progress: int,
                        success_count: int = 0) -> dict[str, Any]:
        """更新升级进度。

        Args:
            progress: 0-100的进度百分比
            success_count: 成功数量
        """
        rows = store.rows(self.MODULE)
        task = None
        for row in rows:
            if row.get("任务编号") == task_id:
                task = row
                break
        if task is None:
            return {"ok": False, "message": f"升级任务 {task_id} 不存在"}

        if task.get("status") not in ("升级中", "待执行"):
            return {
                "ok": False,
                "message": f"任务 {task_id} 状态为 {task.get('status')}，不能更新进度",
            }

        progress = max(0, min(100, progress))
        task["任务进度"] = f"{progress}%"
        task["成功数量"] = success_count
        task["pending"] = progress < 100

        if progress >= 100:
            task["status"] = "已完成"
            task["pending"] = False
        elif progress > 0:
            task["status"] = "升级中"

        return {
            "ok": True,
            "message": f"任务 {task_id} 进度已更新为 {progress}%",
            "task_id": task_id,
            "进度": f"{progress}%",
            "成功数量": success_count,
            "状态": task["status"],
        }

    def rollback(self, task_id: str) -> dict[str, Any]:
        """回滚固件升级任务。"""
        rows = store.rows(self.MODULE)
        task = None
        for row in rows:
            if row.get("任务编号") == task_id:
                task = row
                break
        if task is None:
            return {"ok": False, "message": f"升级任务 {task_id} 不存在"}

        old_status = task.get("status")
        if old_status not in ("已完成", "部分失败"):
            return {
                "ok": False,
                "message": f"任务 {task_id} 状态为 {old_status}，不能回滚",
            }

        task["status"] = "部分失败"
        task["pending"] = True
        task["abnormal"] = True
        task["任务进度"] = "已回滚"

        return {
            "ok": True,
            "message": f"任务 {task_id} 已回滚",
            "原状态": old_status,
            "现状态": "部分失败",
        }

    def ota_summary(self) -> dict[str, Any]:
        """固件升级统计摘要。"""
        rows = store.rows(self.MODULE)
        total = len(rows)
        by_status = {}
        by_strategy = {}
        for row in rows:
            s = row.get("status", "未知")
            by_status[s] = by_status.get(s, 0) + 1

            strategy = row.get("升级策略", "未知")
            by_strategy[strategy] = by_strategy.get(strategy, 0) + 1

        return {
            "total_tasks": total,
            "by_status": by_status,
            "by_strategy": by_strategy,
            "active_tasks": by_status.get("升级中", 0) + by_status.get("待执行", 0),
            "completion_rate": round(
                by_status.get("已完成", 0) / max(total, 1) * 100, 1
            ),
        }

    def check_compatibility(self, device_type: str,
                            firmware_version: str) -> dict[str, Any]:
        """检查设备与固件版本的兼容性。"""
        device_rows = store.rows("device_registry")
        compatible_devices = []
        incompatible = []

        for dev in device_rows:
            if dev.get("设备类型") == device_type:
                current_ver = dev.get("固件版本", "")
                if current_ver.startswith("v"):
                    try:
                        current_minor = int(current_ver.split(".")[1])
                        target_minor = int(firmware_version.split(".")[1])
                        if target_minor >= current_minor:
                            compatible_devices.append(dev.get("设备编号"))
                        else:
                            incompatible.append({
                                "设备编号": dev.get("设备编号"),
                                "当前版本": current_ver,
                                "原因": "目标版本低于当前版本",
                            })
                    except (IndexError, ValueError):
                        compatible_devices.append(dev.get("设备编号"))

        return {
            "ok": True,
            "device_type": device_type,
            "firmware_version": firmware_version,
            "compatible_count": len(compatible_devices),
            "incompatible_count": len(incompatible),
            "compatible_devices": compatible_devices[:10],
            "incompatible": incompatible[:5],
        }

    # ========== 跨模块方法 ==========

    def ota_with_aggregate_reporting(self, target_device: str, firmware_version: str,
                                     upgrade_strategy: str, target_count: int = 10) -> dict[str, Any]:
        """固件升级任务创建时自动创建聚合分发任务汇总进度。

        业务流程:
        1. 创建固件升级任务
        2. 创建聚合分发任务关联
        3. 返回关联任务对
        """
        ota_rows = store.rows(self.MODULE)
        agg_rows = store.rows("aggregate_sink")

        max_id = max((int(row.get("id", 0)) for row in ota_rows), default=0)
        ota_task = {
            "id": max_id + 1,
            "任务编号": f"OTA-{max_id + 1:04d}",
            "目标设备": target_device,
            "固件版本": firmware_version,
            "升级包摘要": f"sha256:mock_{firmware_version}",
            "升级策略": upgrade_strategy,
            "目标数量": target_count,
            "成功数量": 0,
            "任务进度": "0%",
            "status": "待执行",
            "pending": True,
            "abnormal": False,
        }
        ota_rows.append(ota_task)

        max_aid = max((int(row.get("id", 0)) for row in agg_rows), default=0)
        agg_task = {
            "id": max_aid + 1,
            "任务编号": f"AGG-{max_aid + 1:04d}",
            "数据窗口": f"ota_{ota_task['任务编号']}",
            "聚合函数": "count_success",
            "输出Topic": f"ota/status/{target_device}",
            "窗口时长": "300s",
            "滑动步长": "60s",
            "延迟容忍": "30s",
            "执行状态": "待执行",
            "status": "待执行",
            "pending": True,
            "abnormal": False,
        }
        agg_rows.append(agg_task)

        return {
            "ok": True,
            "message": f"固件升级任务已创建, 聚合分发任务已关联",
            "ota_task": ota_task,
            "aggregate_task": agg_task,
        }

    def check_device_registry_for_ota(self, target_device: str) -> dict[str, Any]:
        """检查设备注册信息以决定是否允许OTA升级。

        业务流程:
        1. 查找设备档案
        2. 检查设备状态(在线允许、离线/已注销拒绝)
        3. 检查固件版本
        4. 返回检查结果
        """
        device_rows = store.rows("device_registry")
        ota_rows = store.rows(self.MODULE)

        device = None
        for row in device_rows:
            if row.get("设备编号") == target_device:
                device = row
                break
        if device is None:
            return {"ok": False, "message": f"设备 {target_device} 未注册"}

        dev_status = device.get("status", "")
        if dev_status in ("离线", "已注销"):
            return {"ok": False, "message": f"设备 {target_device} 状态为 {dev_status}，不允许OTA"}

        # 查找已有OTA任务
        existing = [
            r for r in ota_rows
            if r.get("目标设备") == target_device and r.get("status") in ("待执行", "升级中")
        ]

        return {
            "ok": True,
            "message": f"设备 {target_device} 可执行OTA",
            "设备编号": target_device,
            "设备类型": device.get("设备类型"),
            "设备状态": dev_status,
            "当前固件": device.get("固件版本"),
            "协议类型": device.get("协议类型"),
            "已有进行中任务": len(existing),
        }