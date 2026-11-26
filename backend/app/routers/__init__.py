"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import cross_module, resource, trial_receipt

# 试投回单工作流前缀 /api/trial-receipt 需在通用 /api/{module} 之前注册，
# 确保固定路径优先于动态模块路径匹配。
ROUTERS = [cross_module, trial_receipt, resource]
