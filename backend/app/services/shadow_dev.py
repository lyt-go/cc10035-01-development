"""设备影子领域服务：IoT设备影子状态的同步与冲突管理。

提供影子同步、差异检测、冲突解决、版本管理等真实业务能力。
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


class ShadowDevService(BaseModuleService):
    MODULE = "shadow_dev"
    ENTITY = "影子记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["记录编号", "设备编号", "期望状态", "上报状态", "差异字段", "同步时间", "版本号", "冲突解决"]
    REQUIRED_FIELDS = ["记录编号", "设备编号", "期望状态"]
    STATUS_ORDER = ["同步中", "已同步", "存在差异", "冲突待解决"]
    ACTION_RULES = {"发起同步": "已同步", "确认差异": "存在差异", "解决冲突": "冲突待解决"}
    NEGATIVE_ACTIONS = []

    def sync_shadow(self, device_id: str, desired_state: str) -> dict[str, Any]:
        """设备影子同步：更新期望状态，检测与上报状态的差异。

        业务流程：
        1. 查找或创建设备影子记录
        2. 更新期望状态
        3. 检测与上报状态的差异
        4. 更新版本号和同步时间
        """
        rows = store.rows(self.MODULE)
        shadow = None
        for row in rows:
            if row.get("设备编号") == device_id:
                shadow = row
                break

        if shadow is None:
            # 创建新影子
            max_id = max((int(row.get("id", 0)) for row in rows), default=0)
            shadow = {
                "id": max_id + 1,
                "记录编号": f"SHAD-{max_id + 1:04d}",
                "设备编号": device_id,
                "期望状态": desired_state,
                "上报状态": "",
                "差异字段": "",
                "同步时间": _now(),
                "版本号": "1",
                "冲突解决": "",
                "status": "同步中",
                "pending": True,
                "abnormal": False,
            }
            rows.append(shadow)
            return {
                "ok": True,
                "message": f"设备 {device_id} 影子已创建",
                "shadow": shadow,
                "has_diff": False,
            }

        old_desired = shadow.get("期望状态", "")
        reported = shadow.get("上报状态", "")

        # 更新期望状态
        shadow["期望状态"] = desired_state
        shadow["同步时间"] = _now()
        try:
            ver = int(shadow.get("版本号", "0")) + 1
        except (ValueError, TypeError):
            ver = 1
        shadow["版本号"] = str(ver)

        # 差异检测
        has_diff = desired_state != reported
        diff_fields = []
        if has_diff:
            if desired_state != reported:
                diff_fields.append("state")
            shadow["差异字段"] = ",".join(diff_fields)
            shadow["status"] = "存在差异"
        else:
            shadow["差异字段"] = ""
            shadow["status"] = "已同步"

        return {
            "ok": True,
            "message": f"设备 {device_id} 影子已同步",
            "shadow": {
                "设备编号": device_id,
                "期望状态": desired_state,
                "上报状态": reported,
                "版本号": ver,
            },
            "has_diff": has_diff,
            "diff_fields": diff_fields,
        }

    def report_state(self, device_id: str, reported_state: str) -> dict[str, Any]:
        """设备上报状态：更新影子中的上报状态并检测差异。

        业务流程：
        1. 查找设备影子
        2. 更新上报状态
        3. 检测与期望状态的差异
        4. 如有差异则标记为存在差异
        """
        rows = store.rows(self.MODULE)
        shadow = None
        for row in rows:
            if row.get("设备编号") == device_id:
                shadow = row
                break
        if shadow is None:
            return {"ok": False, "message": f"设备 {device_id} 没有影子记录"}

        shadow["上报状态"] = reported_state
        shadow["同步时间"] = _now()

        desired = shadow.get("期望状态", "")
        has_diff = desired != reported_state
        if has_diff:
            shadow["差异字段"] = "state"
            shadow["status"] = "存在差异"
        else:
            shadow["差异字段"] = ""
            shadow["status"] = "已同步"

        try:
            ver = int(shadow.get("版本号", "0")) + 1
        except (ValueError, TypeError):
            ver = 1
        shadow["版本号"] = str(ver)

        return {
            "ok": True,
            "message": f"设备 {device_id} 已上报状态",
            "设备编号": device_id,
            "期望状态": desired,
            "上报状态": reported_state,
            "has_diff": has_diff,
        }

    def resolve_conflict(self, device_id: str, resolution: str) -> dict[str, Any]:
        """解决设备影子冲突：选择期望状态或上报状态作为最终状态。

        Args:
            resolution: "use_desired" 或 "use_reported"
        """
        rows = store.rows(self.MODULE)
        shadow = None
        for row in rows:
            if row.get("设备编号") == device_id:
                shadow = row
                break
        if shadow is None:
            return {"ok": False, "message": f"设备 {device_id} 没有影子记录"}

        desired = shadow.get("期望状态", "")
        reported = shadow.get("上报状态", "")

        if resolution == "use_desired":
            shadow["上报状态"] = desired
            shadow["冲突解决"] = "采纳期望状态"
        elif resolution == "use_reported":
            shadow["期望状态"] = reported
            shadow["冲突解决"] = "采纳上报状态"
        else:
            return {"ok": False, "message": f"无效的冲突解决方式: {resolution}"}

        shadow["差异字段"] = ""
        shadow["status"] = "已同步"
        shadow["同步时间"] = _now()
        try:
            ver = int(shadow.get("版本号", "0")) + 1
        except (ValueError, TypeError):
            ver = 1
        shadow["版本号"] = str(ver)

        return {
            "ok": True,
            "message": f"设备 {device_id} 冲突已解决({resolution})",
            "最终状态": desired if resolution == "use_desired" else reported,
            "解决方案": shadow["冲突解决"],
        }

    def shadow_summary(self) -> dict[str, Any]:
        """设备影子统计摘要。"""
        rows = store.rows(self.MODULE)
        total = len(rows)
        synced = sum(1 for r in rows if r.get("status") == "已同步")
        diff = sum(1 for r in rows if r.get("status") == "存在差异")
        conflict = sum(1 for r in rows if r.get("status") == "冲突待解决")

        return {
            "total_shadows": total,
            "synced": synced,
            "has_diff": diff,
            "conflict": conflict,
            "sync_rate": round(synced / max(total, 1) * 100, 1),
            "shadows": [
                {
                    "设备编号": r.get("设备编号"),
                    "期望状态": r.get("期望状态"),
                    "上报状态": r.get("上报状态"),
                    "状态": r.get("status"),
                    "版本号": r.get("版本号"),
                }
                for r in rows
            ],
        }

    # ========== 跨模块方法 ==========

    def sync_shadow_with_tags(self, device_id: str, desired_state: str) -> dict[str, Any]:
        """设备影子同步时自动查找并更新关联标签。

        业务流程:
        1. 查找/创建设备影子
        2. 查找设备已绑定标签
        3. 根据期望状态追加标签值
        4. 返回影子与标签更新结果
        """
        shadow_rows = store.rows(self.MODULE)
        tag_rows = store.rows("tag_binding")

        shadow = None
        for row in shadow_rows:
            if row.get("设备编号") == device_id:
                shadow = row
                break

        if shadow is None:
            max_id = max((int(row.get("id", 0)) for row in shadow_rows), default=0)
            shadow = {
                "id": max_id + 1,
                "记录编号": f"SHAD-{max_id + 1:04d}",
                "设备编号": device_id,
                "期望状态": desired_state,
                "上报状态": "",
                "差异字段": "",
                "同步时间": _now(),
                "版本号": "1",
                "冲突解决": "",
                "status": "同步中",
                "pending": True,
                "abnormal": False,
            }
            shadow_rows.append(shadow)
        else:
            old_desired = shadow.get("期望状态", "")
            shadow["期望状态"] = desired_state
            shadow["同步时间"] = _now()
            shadow["status"] = "存在差异" if desired_state != old_desired else "已同步"

        # 查找关联标签
        related_tags = [
            row for row in tag_rows
            if row.get("设备编号") == device_id and row.get("status") == "已绑定"
        ]

        tag_updates = []
        for tag in related_tags:
            old_value = tag.get("标签值", "")
            new_value = f"{old_value}+{desired_state}"
            tag["标签值"] = new_value
            try:
                tag["标签版本"] = str(int(tag.get("标签版本", "0")) + 1)
            except (ValueError, TypeError):
                tag["标签版本"] = "1"
            tag_updates.append({
                "tag_key": tag.get("标签键"),
                "old_value": old_value,
                "new_value": new_value,
            })

        return {
            "ok": True,
            "message": f"设备 {device_id} 影子已同步, {len(tag_updates)} 个标签已更新",
            "shadow": {"设备编号": device_id, "期望状态": desired_state, "同步时间": shadow["同步时间"]},
            "tag_updates": tag_updates,
        }

    def tag_aware_shadow_summary(self) -> list[dict[str, Any]]:
        """查询设备影子与标签绑定的关联概览。

        返回每个影子对应设备的已绑定标签数。
        """
        shadow_rows = store.rows(self.MODULE)
        tag_rows = store.rows("tag_binding")
        result = []
        for s in shadow_rows:
            dev_id = s.get("设备编号")
            tag_count = sum(
                1 for t in tag_rows
                if t.get("设备编号") == dev_id and t.get("status") == "已绑定"
            )
            result.append({
                "设备编号": dev_id,
                "期望状态": s.get("期望状态"),
                "上报状态": s.get("上报状态"),
                "影子状态": s.get("status"),
                "已绑标签数": tag_count,
            })
        return result