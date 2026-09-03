"""Public, credential-free trading domain library."""

from . import paper_execution as paper_execution
from . import paper_exit as paper_exit
from .paper_exit import close_open_position
from .paper_session_flatten import flatten_open_position_at_session_close
from .pending_cancel import cancel_pending
from .pending_replace import replace_pending_limit
from .pending_session_close import cancel_pending_for_session_close
from .pending_stop import process_pending_stop

paper_execution.close_open_position = close_open_position
paper_execution.DeterministicPaperBroker.cancel_pending = cancel_pending
paper_execution.DeterministicPaperBroker.replace_pending_limit = replace_pending_limit
paper_execution.DeterministicPaperBroker.cancel_pending_for_session_close = cancel_pending_for_session_close
paper_execution.DeterministicPaperBroker.process_pending_stop = process_pending_stop
paper_exit.flatten_open_position_at_session_close = flatten_open_position_at_session_close

__all__ = ["close_open_position", "flatten_open_position_at_session_close", "paper_execution"]
