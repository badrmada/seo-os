"""Output sinks — where a finished run's result goes (PLAN.md Step 5).

See tools/base.py's OutputSink Protocol for the contract, and
agent/managers/output_manager.py for how AgentConfig.output_sinks becomes concrete
sink instances.
"""

from .json_sink import JsonOutputSink
from .webhook_sink import WebhookOutputSink

__all__ = ["JsonOutputSink", "WebhookOutputSink"]
