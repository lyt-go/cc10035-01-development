"""时序存储领域服务：时序数据的存储策略、压缩与生命周期管理。

提供存储策略优化、压缩比估算、冷热数据分层计算等真实业务能力。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


class TsStoreService(BaseModuleService):
    MODULE = "ts_store"
    ENTITY = "存储策略"
    OPERATION_TERM = "存储"
    LIST_FIELDS = ["策略编号", "测点编号", "存储引擎", "压缩算法", "降采样规则", "保留天数", "分段策略", "冷热分层"]
    REQUIRED_FIELDS = ["策略编号", "测点编号", "存储引擎"]
    STATUS_ORDER = ["草稿", "待验证", "已生效", "已停用"]
    ACTION_RULES = {"提交验证": "待验证", "标记生效": "已生效", "停用策略": "已停用"}
    NEGATIVE_ACTIONS = ["停用策略"]

    def estimate_storage(self, point_id: str, data_points_per_day: int = 86400) -> dict[str, Any]:
        """存储空间估算：根据采集间隔和保留天数预估存储需求。

        业务流程：
        1. 查找测点的存储策略
        2. 根据采集间隔计算日数据量
        3. 乘以保留天数
        4. 考虑压缩比
        """
        rows = store.rows(self.MODULE)
        strategy = None
        for row in rows:
            if row.get("测点编号") == point_id and row.get("status") == "已生效":
                strategy = row
                break
        if strategy is None:
            return {"ok": False, "message": f"测点 {point_id} 无已生效的存储策略"}

        try:
            retention_days = int(strategy.get("保留天数", "30"))
        except (ValueError, TypeError):
            retention_days = 30

        compressor = strategy.get("压缩算法", "none")
        compression_ratio = {"gzip": 5, "snappy": 2, "lz4": 3, "zstd": 6, "none": 1}.get(
            compressor, 2
        )

        # 每条记录约 32 字节 (timestamp + value)
        bytes_per_point = 32
        raw_bytes = data_points_per_day * bytes_per_point * retention_days
        compressed_bytes = raw_bytes / compression_ratio

        cold_days = int((strategy.get("冷热分层", "30d_hot")).replace("d_hot", ""))
        hot_bytes = data_points_per_day * bytes_per_point * cold_days / compression_ratio

        return {
            "ok": True,
            "point_id": point_id,
            "retention_days": retention_days,
            "compression_algorithm": compressor,
            "compression_ratio": compression_ratio,
            "raw_bytes_estimate": raw_bytes,
            "compressed_bytes_estimate": compressed_bytes,
            "hot_storage_bytes": hot_bytes,
            "cold_storage_bytes": compressed_bytes - hot_bytes,
            "storage_engine": strategy.get("存储引擎"),
        }

    def optimize_retention(self, min_free_space_mb: float = 1024) -> dict[str, Any]:
        """保留策略优化：根据预估存储空间调整各测点的保留天数。

        超过空间限制时，按采集间隔降序缩减保留天数。
        """
        rows = store.rows(self.MODULE)
        point_rows = store.rows("ts_point")

        # 构建测点->采集间隔映射
        interval_map = {}
        for pt in point_rows:
            try:
                interval = int(pt.get("采集间隔", "10").replace("s", ""))
            except (ValueError, TypeError):
                interval = 10
            interval_map[pt.get("测点编号")] = interval

        recommendations = []
        total_estimated_mb = 0
        for strategy in rows:
            if strategy.get("status") != "已生效":
                continue
            pt_id = strategy.get("测点编号")
            interval = interval_map.get(pt_id, 10)
            daily_points = 86400 // interval
            try:
                retention = int(strategy.get("保留天数", "30"))
            except (ValueError, TypeError):
                retention = 30

            # 估算存储
            raw_bytes = daily_points * 32 * retention
            total_estimated_mb += raw_bytes / (1024 * 1024)

            # 保留天数越短越好
            recommended_retention = max(7, min(retention, retention * 2 // 3))
            recommendations.append({
                "测点编号": pt_id,
                "当前保留天数": retention,
                "建议保留天数": recommended_retention,
                "每日数据点": daily_points,
            })

        return {
            "total_estimated_mb": round(total_estimated_mb, 2),
            "min_free_space_mb": min_free_space_mb,
            "need_optimize": total_estimated_mb > min_free_space_mb,
            "recommendations": recommendations,
        }