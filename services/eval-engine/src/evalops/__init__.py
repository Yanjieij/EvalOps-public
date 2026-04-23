"""EvalOps evaluation engine.

A Python package that runs benchmarks against a System Under Test (SUT),
scores the outputs with a hybrid judge engine, and emits structured results
suitable for dashboards, regression tracking, and data-flywheel harvesting.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("evalops-eval-engine")
except PackageNotFoundError:  # running from source without install
    __version__ = "0.1.0-dev"

__all__ = ["__version__"]
