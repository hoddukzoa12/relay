try:
    # Exposes `root_agent` for `adk run` / `adk web`. Guarded so the FastAPI
    # server path never breaks if ADK isn't importable in some environment.
    from . import agent  # noqa: F401
except Exception:  # noqa: BLE001
    pass
