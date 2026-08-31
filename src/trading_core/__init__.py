"""Public, credential-free trading domain library."""

from . import paper_execution as paper_execution
from .paper_exit import close_open_position

paper_execution.close_open_position = close_open_position

__all__ = ["close_open_position", "paper_execution"]
