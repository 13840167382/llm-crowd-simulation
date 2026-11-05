"""领域枚举。"""

from backend.app.domain.enums.analytics import HeatmapMetric, NormalizationMode
from backend.app.domain.enums.allocation import DistributionMode
from backend.app.domain.enums.decision import DecisionBatchStatus, DecisionSource
from backend.app.domain.enums.experiment import CompletionReason, ExperimentStatus
from backend.app.domain.enums.maintenance import MaintenanceJobStatus, MaintenanceJobType
from backend.app.domain.enums.topology import ExitType, ZoneType
from backend.app.domain.enums.trajectory import AgentState

__all__ = [
    "AgentState",
    "CompletionReason",
    "DecisionBatchStatus",
    "DecisionSource",
    "DistributionMode",
    "ExitType",
    "ExperimentStatus",
    "HeatmapMetric",
    "MaintenanceJobStatus",
    "MaintenanceJobType",
    "NormalizationMode",
    "ZoneType",
]
