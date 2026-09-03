from __future__ import annotations

from .paper_execution import PaperOrder


def cancel_pending_for_session_close(self, order: PaperOrder, intent, *, occurred_at):
    """Cancel a pending paper LIMIT/STOP at session close without fabricating a fill."""
    return self.cancel_pending(order, intent, occurred_at=occurred_at)
