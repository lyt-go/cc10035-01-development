"""告警触发领域服务：IoT测点阈值告警的全生命周期管理。

提供告警评估、批量检测、通知策略、告警统计等真实业务能力。
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


class AlarmTriggerService(BaseModuleService):
    MODULE = "alarm_trigger"
    ENTITY = "告警规则"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["规则编号", "关联测点", "告警条件", "阈值上限", "阈值下限", "持续秒数", "告警级别", "通知方式"]
    REQUIRED_FIELDS = ["规则编号", "关联测点", "告警条件"]
    STATUS_ORDER = ["草稿", "待验证", "已启用", "已静默"]
    ACTION_RULES = {"提交验证": "待验证", "启用规则": "已启用", "静默规则": "已静默"}
    NEGATIVE_ACTIONS = []

    def evaluate_threshold(self, point_id: str, current_value: float) -> dict[str, Any]:
        """评估单个测点是否触发告警阈值。

        业务流程：
        1. 查找该测点的已启用告警规则
        2. 对比阈值上下限
        3. 返回触发结果和告警详情
        """
        rows = store.rows(self.MODULE)
        triggered = []
        for alarm in rows:
            if alarm.get("关联测点") != point_id or alarm.get("status") != "已启用":
                continue
            try:
                upper = float(alarm.get("阈值上限", "inf"))
            except (ValueError, TypeError):
                upper = float("inf")
            try:
                lower = float(alarm.get("阈值下限", "-inf"))
            except (ValueError, TypeError):
                lower = float("-inf")

            condition = alarm.get("告警条件", "")
            hit = False
            if ">" in condition:
                hit = current_value > upper
            elif "<" in condition:
                hit = current_value < lower
            elif "between" in condition.lower():
                hit = lower <= current_value <= upper
            else:
                hit = current_value > upper or current_value < lower

            if hit:
                triggered.append({
                    "规则编号": alarm.get("规则编号"),
                    "告警级别": alarm.get("告警级别"),
                    "阈值上限": upper,
                    "阈值下限": lower,
                    "当前值": current_value,
                    "通知方式": alarm.get("通知方式"),
                    "触发时间": _now(),
                })

        return {
            "ok": True,
            "point_id": point_id,
            "current_value": current_value,
            "triggered_count": len(triggered),
            "alarms": triggered,
        }

    def batch_check(self, points: list[dict[str, float]]) -> dict[str, Any]:
        """批量检查多个测点的告警状态。

        Args:
            points: [{"point_id": "x", "value": 1.0}, ...]
        """
        results = []
        triggered_count = 0
        for pt in points:
            res = self.evaluate_threshold(pt.get("point_id", ""), pt.get("value", 0))
            results.append(res)
            if res["triggered_count"] > 0:
                triggered_count += 1

        return {
            "ok": True,
            "total_points": len(points),
            "triggered_count": triggered_count,
            "healthy_count": len(points) - triggered_count,
            "details": results,
        }

    def alarm_statistics(self) -> dict[str, Any]:
        """告警规则统计：按级别、状态和关联测点分布。"""
        rows = store.rows(self.MODULE)
        by_level = {}
        by_status = {}
        by_point = {}

        for row in rows:
            level = row.get("告警级别", "未知")
            by_level[level] = by_level.get(level, 0) + 1

            status = row.get("status", "未知")
            by_status[status] = by_status.get(status, 0) + 1

            point = row.get("关联测点", "未知")
            by_point[point] = by_point.get(point, 0) + 1

        return {
            "total_rules": len(rows),
            "enabled_count": by_status.get("已启用", 0),
            "by_level": by_level,
            "by_status": by_status,
            "by_point": [
                {"测点": k, "告警规则数": v}
                for k, v in sorted(by_point.items(), key=lambda x: x[1], reverse=True)
            ],
        }

    # ========== 跨模块方法 ==========

    def evaluate_with_rule_engine(self, point_id: str, current_value: float) -> dict[str, Any]:
        """告警触发后自动查找联动规则引擎执行对应动作。

        业务流程：
        1. 评估告警阈值
        2. 如触发告警，查找rule_engine中已启用的联动规则
        3. 执行匹配的动作并记录
        """
        alarm_rows = store.rows(self.MODULE)
        rule_rows = store.rows("rule_engine")

        triggered = []
        for alarm in alarm_rows:
            if alarm.get("关联测点") != point_id or alarm.get("status") != "已启用":
                continue
            try:
                upper = float(alarm.get("阈值上限", "inf"))
            except (ValueError, TypeError):
                upper = float("inf")
            try:
                lower = float(alarm.get("阈值下限", "-inf"))
            except (ValueError, TypeError):
                lower = float("-inf")

            condition = alarm.get("告警条件", "")
            hit = False
            if ">" in condition:
                hit = current_value > upper
            elif "<" in condition:
                hit = current_value < lower
            else:
                hit = current_value > upper or current_value < lower

            if hit:
                triggered.append(alarm)

        if not triggered:
            return {"ok": True, "triggered": False, "message": f"测点 {point_id} 值 {current_value} 未触发任何告警"}

        # 查找联动规则
        actions = []
        for alarm in triggered:
            for rule in rule_rows:
                if rule.get("status") != "已启用":
                    continue
                try:
                    exec_count = int(rule.get("执行次数", "0")) + 1
                except (ValueError, TypeError):
                    exec_count = 1
                rule["执行次数"] = str(exec_count)
                actions.append({
                    "alarm_id": alarm.get("规则编号"),
                    "alarm_level": alarm.get("告警级别"),
                    "rule_engine_id": rule.get("规则编号"),
                    "action": rule.get("执行动作"),
                    "params": rule.get("动作参数"),
                    "exec_count": exec_count,
                })

        return {
            "ok": True,
            "triggered": True,
            "point_id": point_id,
            "current_value": current_value,
            "triggered_alarms": len(triggered),
            "rule_actions": actions,
        }

    def correlate_with_device_status(self, device_id: str) -> dict[str, Any]:
        """将告警规则关联到设备注册状态, 按设备在线状况调整告警规则。

        业务流程：
        1. 查找设备信息
        2. 查找该设备关联的所有测点
        3. 汇总各测点的告警规则状态
        """
        device_rows = store.rows("device_registry")
        point_rows = store.rows("ts_point")
        alarm_rows = store.rows(self.MODULE)

        device = None
        for row in device_rows:
            if row.get("设备编号") == device_id:
                device = row
                break
        if device is None:
            return {"ok": False, "message": f"设备 {device_id} 不存在"}

        # 找到该设备的所有测点
        device_points = [
            pt for pt in point_rows
            if pt.get("设备编号") == device_id
        ]

        device_point_ids = {pt.get("测点编号") for pt in device_points}

        correlated = []
        for alarm in alarm_rows:
            if alarm.get("关联测点") in device_point_ids:
                correlated.append({
                    "规则编号": alarm.get("规则编号"),
                    "关联测点": alarm.get("关联测点"),
                    "告警条件": alarm.get("告警条件"),
                    "告警级别": alarm.get("告警级别"),
                    "规则状态": alarm.get("status"),
                })

        return {
            "ok": True,
            "设备编号": device_id,
            "设备状态": device.get("status"),
            "设备类型": device.get("设备类型"),
            "关联测点数": len(device_points),
            "告警规则数": len(correlated),
            "correlated": correlated,
        }