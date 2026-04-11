"""Session context holder for propagating session_id through the call chain."""
import contextvars

_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="default")
_current_agent_id: contextvars.ContextVar[str] = contextvars.ContextVar("agent_id", default="claw-aie")


def set_session(session_id: str, agent_id: str = "claw-aie") -> None:
    """Set the current session and agent IDs."""
    _current_session_id.set(session_id)
    _current_agent_id.set(agent_id)


def get_session() -> tuple[str, str]:
    """Return the current (session_id, agent_id) tuple."""
    return _current_session_id.get(), _current_agent_id.get()
