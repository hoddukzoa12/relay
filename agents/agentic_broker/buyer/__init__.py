try:
    from . import agent  # noqa: F401  (exposes root_agent for `adk run`/`adk web`)
except Exception:  # noqa: BLE001
    pass
