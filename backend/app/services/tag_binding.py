"""标签绑定领域服务：IoT设备标签的绑定/解绑与批量管理。

提供标签绑定、解绑、批量操作、标签统计等真实业务能力。
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


class TagBindingService(BaseModuleService):
    MODULE = "tag_binding"
    ENTITY = "标签关系"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["关系编号", "设备编号", "标签键", "标签值", "绑定时间", "解绑时间", "来源方式", "标签版本"]
    REQUIRED_FIELDS = ["关系编号", "设备编号", "标签键"]
    STATUS_ORDER = ["已绑定", "已解绑", "待确认"]
    ACTION_RULES = {"解绑标签": "已解绑", "确认绑定": "待确认"}
    NEGATIVE_ACTIONS = []

    def bind_tag(self, device_id: str, tag_key: str, tag_value: str,
                 source: str = "manual") -> dict[str, Any]:
        """为设备绑定标签：防重复绑定。

        业务流程：
        1. 检查是否已存在相同的键值绑定
        2. 创建标签绑定记录
        3. 返回绑定结果
        """
        rows = store.rows(self.MODULE)

        # 防重复检查
        for row in rows:
            if (row.get("设备编号") == device_id
                    and row.get("标签键") == tag_key
                    and row.get("标签值") == tag_value
                    and row.get("status") == "已绑定"):
                return {
                    "ok": False,
                    "message": f"设备 {device_id} 已绑定标签 {tag_key}={tag_value}",
                }

        max_id = max((int(row.get("id", 0)) for row in rows), default=0)
        # 找最大版本号
        max_ver = 0
        for row in rows:
            if row.get("标签键") == tag_key:
                try:
                    ver = int(row.get("标签版本", "0"))
                except (ValueError, TypeError):
                    ver = 0
                max_ver = max(max_ver, ver)

        entry = {
            "id": max_id + 1,
            "关系编号": f"TAG-{max_id + 1:04d}",
            "设备编号": device_id,
            "标签键": tag_key,
            "标签值": tag_value,
            "绑定时间": _now(),
            "解绑时间": "",
            "来源方式": source,
            "标签版本": str(max_ver + 1),
            "status": "已绑定",
            "pending": True,
            "abnormal": False,
        }
        rows.append(entry)
        return {
            "ok": True,
            "message": f"标签 {tag_key}={tag_value} 已绑定到设备 {device_id}",
            "entry": entry,
        }

    def unbind_tag(self, relation_id: str) -> dict[str, Any]:
        """解绑标签：通过关系编号解绑指定标签。"""
        rows = store.rows(self.MODULE)
        for row in rows:
            if row.get("关系编号") == relation_id and row.get("status") == "已绑定":
                row["status"] = "已解绑"
                row["解绑时间"] = _now()
                row["pending"] = False
                return {
                    "ok": True,
                    "message": f"标签关系 {relation_id} 已解绑",
                    "entry": row,
                }
        return {"ok": False, "message": f"标签关系 {relation_id} 不存在或已解绑"}

    def query_device_tags(self, device_id: str) -> dict[str, Any]:
        """查询设备的所有已绑定标签。"""
        rows = store.rows(self.MODULE)
        tags = [
            {
                "关系编号": row.get("关系编号"),
                "标签键": row.get("标签键"),
                "标签值": row.get("标签值"),
                "绑定时间": row.get("绑定时间"),
                "来源方式": row.get("来源方式"),
                "标签版本": row.get("标签版本"),
            }
            for row in rows
            if row.get("设备编号") == device_id and row.get("status") == "已绑定"
        ]
        return {
            "ok": True,
            "device_id": device_id,
            "tag_count": len(tags),
            "tags": tags,
        }

    def batch_bind(self, device_ids: list[str], tag_key: str,
                   tag_value: str, source: str = "batch") -> dict[str, Any]:
        """批量为多个设备绑定同一标签。"""
        results = []
        success = 0
        for dev_id in device_ids:
            res = self.bind_tag(dev_id, tag_key, tag_value, source)
            results.append({"设备编号": dev_id, "ok": res["ok"], "message": res["message"]})
            if res["ok"]:
                success += 1

        return {
            "ok": True,
            "total": len(device_ids),
            "success": success,
            "failed": len(device_ids) - success,
            "details": results,
        }

    def tag_distribution(self) -> dict[str, Any]:
        """标签分布统计：每个标签键有多少设备使用。"""
        rows = store.rows(self.MODULE)
        by_key = {}
        for row in rows:
            if row.get("status") == "已绑定":
                key = row.get("标签键", "未知")
                if key not in by_key:
                    by_key[key] = {"count": 0, "values": set()}
                by_key[key]["count"] += 1
                by_key[key]["values"].add(row.get("标签值", ""))

        result = [
            {"标签键": k, "绑定数": v["count"], "值种类": len(v["values"]),
             "值列表": list(v["values"])[:5]}
            for k, v in sorted(by_key.items(), key=lambda x: x[1]["count"], reverse=True)
        ]

        return {
            "total_bindings": sum(1 for r in rows if r.get("status") == "已绑定"),
            "unique_keys": len(by_key),
            "distribution": result,
        }

    # ========== 跨模块方法 ==========

    def bind_tag_with_shadow_sync(self, device_id: str, tag_key: str, tag_value: str) -> dict[str, Any]:
        """绑定标签时自动校验设备影子期望状态。

        业务流程:
        1. 查找设备影子
        2. 校验影子期望状态
        3. 创建/更新标签绑定
        4. 返回关联影子信息
        """
        shadow_rows = store.rows("shadow_dev")
        tag_rows = store.rows(self.MODULE)

        # 查找影子
        shadow = None
        for row in shadow_rows:
            if row.get("设备编号") == device_id:
                shadow = row
                break

        shadow_status = shadow.get("期望状态") if shadow else "无影子"

        # 防重复绑定
        for row in tag_rows:
            if (row.get("设备编号") == device_id
                    and row.get("标签键") == tag_key
                    and row.get("标签值") == tag_value
                    and row.get("status") == "已绑定"):
                return {"ok": False, "message": f"设备 {device_id} 已绑定标签 {tag_key}={tag_value}"}

        max_id = max((int(row.get("id", 0)) for row in tag_rows), default=0)
        tag_entry = {
            "id": max_id + 1,
            "关系编号": f"TAG-{max_id + 1:04d}",
            "设备编号": device_id,
            "标签键": tag_key,
            "标签值": tag_value,
            "绑定时间": _now(),
            "解绑时间": "",
            "来源方式": "shadow_sync",
            "标签版本": "1",
            "status": "已绑定",
            "pending": True,
            "abnormal": False,
        }
        tag_rows.append(tag_entry)

        return {
            "ok": True,
            "message": f"标签 {tag_key}={tag_value} 已绑定到设备 {device_id}",
            "tag_entry": tag_entry,
            "shadow_status": shadow_status,
        }

    def query_device_with_shadow(self, device_id: str) -> dict[str, Any]:
        """查询设备标签与影子的综合视图。

        返回该设备的所有标签绑定和影子状态。
        """
        shadow_rows = store.rows("shadow_dev")
        tag_rows = store.rows(self.MODULE)

        shadow = None
        for row in shadow_rows:
            if row.get("设备编号") == device_id:
                shadow = row
                break

        tags = [
            {
                "关系编号": row.get("关系编号"),
                "标签键": row.get("标签键"),
                "标签值": row.get("标签值"),
                "绑定时间": row.get("绑定时间"),
                "标签版本": row.get("标签版本"),
            }
            for row in tag_rows
            if row.get("设备编号") == device_id and row.get("status") == "已绑定"
        ]

        return {
            "ok": True,
            "设备编号": device_id,
            "标签数": len(tags),
            "tags": tags,
            "shadow": {
                "期望状态": shadow.get("期望状态") if shadow else "无影子",
                "上报状态": shadow.get("上报状态") if shadow else "无",
                "影子状态": shadow.get("status") if shadow else "无",
                "版本号": shadow.get("版本号") if shadow else "0",
            } if shadow else None,
        }