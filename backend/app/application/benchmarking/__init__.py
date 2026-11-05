"""正式压测相关应用服务。"""

from backend.app.application.benchmarking.service import run_formal_benchmark
from backend.app.application.benchmarking.p0_suite import run_p0_benchmark_suite

__all__ = ["run_formal_benchmark", "run_p0_benchmark_suite"]
