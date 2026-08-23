"""
Dirigera MQTT Bridge.

Usable two ways:

1. As an application — run `dirigera_bridge` (the console script) or
   `python -m dirigera_bridge`, configured entirely via .env. See
   main.py / README.md for the Docker/CLI deployment.

2. As a library — import build_orchestrator() to embed this bridge
   inside a larger application, e.g. one that also wires up other
   vendor bridges (Philips Hue, etc.) alongside this one:

       from dirigera_bridge import Settings, load_settings, build_orchestrator

       settings = load_settings()  # reads .env, or construct Settings() directly
       orchestrator = build_orchestrator(settings)
       await orchestrator.run()

   Each build_orchestrator() result is fully self-contained — it
   owns its own MQTT connection — so running several bridges
   concurrently just means running several Orchestrators side by
   side; there's no shared connection state to coordinate between
   them.
"""

from .config import Settings, load_settings
from .factory import build_orchestrator
from .orchestrator import Orchestrator

__all__ = [
    "Orchestrator",
    "Settings",
    "load_settings",
    "build_orchestrator",
]
