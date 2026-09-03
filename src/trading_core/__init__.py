"""Public, credential-free trading domain library."""

from . import paper_execution as paper_execution
from .paper_exit import close_open_position
from .pending_stop import process_pending_stop

paper_execution.close_open_position = close_open_position
paper_execution.DeterministicPaperBroker.process_pending_stop = process_pending_stop

__all__ = ["close_open_position", "paper_execution"]
