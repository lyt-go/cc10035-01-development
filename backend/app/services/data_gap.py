"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class DataGapService(BaseModuleService):
    MODULE = "data_gap"
    ENTITY = "断点记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["记录编号", "设备编号", "测点编号", "中断起始", "中断结束", "缺失点数", "补采策略", "处理状态"]
    REQUIRED_FIELDS = ["记录编号", "设备编号", "测点编号"]
    STATUS_ORDER = ["待补采", "补采中", "已补齐", "放弃补采"]
    ACTION_RULES = {"发起补采": "补采中", "标记补齐": "已补齐", "放弃补采": "放弃补采"}
    NEGATIVE_ACTIONS = []
