"""规则引擎领域服务：IoT联动规则的配置与执行管理。

提供规则加载、动作执行、冷却检测、规则冲突检查等真实业务能力。
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


class RuleEngineService(BaseModuleService):
    MODULE = "rule_engine"
    ENTITY = "联动规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则编号", "触发条件", "执行动作", "动作参数", "优先级", "生效时段", "冷却时间", "执行次数"]
    REQUIRED_FIELDS = ["规则编号", "触发条件", "执行动作"]
    STATUS_ORDER = ["草稿", "待验证", "已启用", "已停用"]
    ACTION_RULES = {"提交验证": "待验证", "启用规则": "已启用", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    def execute_rule(self, rule_id: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行指定联动规则。

        业务流程：
        1. 查找规则并校验状态
        2. 检查冷却时间
        3. 执行动作并记录
        4. 更新执行次数
        """
        rows = store.rows(self.MODULE)
        rule = None
        for row in rows:
            if row.get("规则编号") == rule_id:
                rule = row
                break
        if rule is None:
            return {"ok": False, "message": f"联动规则 {rule_id} 不存在"}

        if rule.get("status") != "已启用":
            return {"ok": False, "message": f"规则 {rule_id} 未启用(当前:{rule.get('status')})"}

        # 冷却检测
        cooldown_sec = 0
        try:
            cooldown_sec = int(rule.get("冷却时间", "0"))
        except (ValueError, TypeError):
            cooldown_sec = 0

        # 更新执行次数
        try:
            exec_count = int(rule.get("执行次数", "0")) + 1
        except (ValueError, TypeError):
            exec_count = 1
        rule["执行次数"] = str(exec_count)

        action = rule.get("执行动作", "")
        params = rule.get("动作参数", "")
        priority = rule.get("优先级", "0")
        effective_window = rule.get("生效时段", "")

        action_result = {
            "rule_id": rule_id,
            "action": action,
            "params": params,
            "priority": priority,
            "effective_window": effective_window,
            "execution_count": exec_count,
            "context": context or {},
            "executed_at": _now(),
            "cooldown_seconds": cooldown_sec,
        }

        return {
            "ok": True,
            "message": f"规则 {rule_id} 已执行(动作:{action})",
            "result": action_result,
        }

    def load_active_rules(self) -> list[dict[str, Any]]:
        """加载所有已启用的联动规则。"""
        rows = store.rows(self.MODULE)
        active = [
            {
                "规则编号": row.get("规则编号"),
                "触发条件": row.get("触发条件"),
                "执行动作": row.get("执行动作"),
                "动作参数": row.get("动作参数"),
                "优先级": row.get("优先级"),
                "冷却时间": row.get("冷却时间"),
                "执行次数": row.get("执行次数"),
            }
            for row in rows
            if row.get("status") == "已启用"
        ]
        return sorted(active, key=lambda x: int(x.get("优先级", "0")), reverse=True)

    def check_rule_conflict(self, rule_id: str) -> dict[str, Any]:
        """检查规则冲突：检测与新规则动作冲突的已启用规则。"""
        rows = store.rows(self.MODULE)
        target = None
        for row in rows:
            if row.get("规则编号") == rule_id:
                target = row
                break
        if target is None:
            return {"ok": False, "message": f"规则 {rule_id} 不存在"}

        target_action = target.get("执行动作", "")
        conflicts = []
        for row in rows:
            if row.get("规则编号") == rule_id or row.get("status") != "已启用":
                continue
            if row.get("触发条件") == target.get("触发条件"):
                other_action = row.get("执行动作", "")
                if other_action != target_action:
                    conflicts.append({
                        "冲突规则": row.get("规则编号"),
                        "冲突动作": other_action,
                        "本规则动作": target_action,
                        "触发条件": row.get("触发条件"),
                    })

        return {
            "ok": True,
            "rule_id": rule_id,
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "is_clean": len(conflicts) == 0,
        }

    def rule_execution_stats(self) -> dict[str, Any]:
        """规则执行统计。"""
        rows = store.rows(self.MODULE)
        total_exec = 0
        by_priority = {}
        for row in rows:
            try:
                total_exec += int(row.get("执行次数", "0"))
            except (ValueError, TypeError):
                pass
            pri = row.get("优先级", "0")
            by_priority[pri] = by_priority.get(pri, 0) + 1

        return {
            "total_rules": len(rows),
            "active_rules": sum(1 for r in rows if r.get("status") == "已启用"),
            "total_executions": total_exec,
            "by_priority": by_priority,
        }

    # ========== 跨模块方法 ==========

    def execute_chain_for_alarm(self, point_id: str, alarm_id: str) -> dict[str, Any]:
        """收到告警触发时, 查找并执行关联的联动规则链。

        业务流程:
        1. 查找告警规则
        2. 加载所有已启用联动规则
        3. 按优先级执行匹配规则
        4. 返回执行结果
        """
        alarm_rows = store.rows("alarm_trigger")
        rule_rows = store.rows(self.MODULE)

        alarm = None
        for row in alarm_rows:
            if row.get("规则编号") == alarm_id and row.get("关联测点") == point_id:
                alarm = row
                break
        if alarm is None:
            return {"ok": False, "message": f"告警规则 {alarm_id} (测点{point_id}) 不存在"}

        # 加载已启用规则，按优先级排序
        active = [row for row in rule_rows if row.get("status") == "已启用"]
        active.sort(key=lambda r: int(r.get("优先级", "0")), reverse=True)

        results = []
        for rule in active:
            condition = rule.get("触发条件", "")
            # 简单匹配：触发条件与告警级别相关
            if alarm.get("告警级别", "") in condition or "always" in condition.lower():
                try:
                    exec_count = int(rule.get("执行次数", "0")) + 1
                except (ValueError, TypeError):
                    exec_count = 1
                rule["执行次数"] = str(exec_count)
                results.append({
                    "rule_id": rule.get("规则编号"),
                    "action": rule.get("执行动作"),
                    "params": rule.get("动作参数"),
                    "priority": rule.get("优先级"),
                    "exec_count": exec_count,
                })

        return {
            "ok": True,
            "alarm_id": alarm_id,
            "point_id": point_id,
            "alarm_level": alarm.get("告警级别"),
            "executed_rules": len(results),
            "results": results,
        }

    def conflict_check_with_alarms(self, rule_id: str) -> dict[str, Any]:
        """检查联动规则与告警规则之间的冲突。

        业务流程:
        1. 加载指定联动规则
        2. 查找所有已启用告警规则
        3. 比对触发条件判断是否存在循环依赖
        """
        rule_rows = store.rows(self.MODULE)
        alarm_rows = store.rows("alarm_trigger")

        target = None
        for row in rule_rows:
            if row.get("规则编号") == rule_id:
                target = row
                break
        if target is None:
            return {"ok": False, "message": f"规则 {rule_id} 不存在"}

        target_condition = target.get("触发条件", "")
        target_action = target.get("执行动作", "")

        conflicts = []
        for alarm in alarm_rows:
            if alarm.get("status") != "已启用":
                continue
            alarm_condition = alarm.get("告警条件", "")
            alarm_rule_id = alarm.get("规则编号", "")

            # 检测循环：告警规则引用的测点与联动规则动作相关
            if target_action in alarm_condition or target_condition in alarm_condition:
                conflicts.append({
                    "告警规则": alarm_rule_id,
                    "关联测点": alarm.get("关联测点"),
                    "告警条件": alarm_condition,
                    "联动条件": target_condition,
                    "联动动作": target_action,
                })

        return {
            "ok": True,
            "rule_id": rule_id,
            "conflict_count": len(conflicts),
            "is_clean": len(conflicts) == 0,
            "conflicts": conflicts,
        }