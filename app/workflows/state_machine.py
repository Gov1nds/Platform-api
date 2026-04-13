"""
Re-export from canonical location.

The authoritative state machine implementation lives in
``app.services.workflow.state_machine``. This module re-exports
all public symbols for backward compatibility with any code that
imports from ``app.workflows.state_machine``.
"""
from app.services.workflow.state_machine import *  # noqa: F401, F403
