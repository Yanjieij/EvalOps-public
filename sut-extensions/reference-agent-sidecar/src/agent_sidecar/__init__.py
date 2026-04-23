"""Reference-app agent sidecar.

A minimal, additive agent surface that EvalOps can exercise. It lives in
its own service directory under a sample app tree so zero existing
files need to change. In a "real" production PR this code would be folded
into the Go gateway + Python ai-engine, exposed through the existing
gRPC AIService as a new AgentRun RPC. For Week 1 we ship it as a sidecar
for fast iteration.
"""

__version__ = "0.1.0"
