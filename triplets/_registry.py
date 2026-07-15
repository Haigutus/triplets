"""Shared lazy engine registry for the parser / sparql / validation packages.

One pattern in one place: engine name → module imported on first use, alias
resolution, "auto" preference order, and an actionable error when an engine
(or every auto candidate) is unavailable. Each package instantiates
EngineRegistry with its engines and keeps thin public register_engine /
get_engine wrappers, so the per-package API is unchanged.

The registry contract for engine modules: importing the module must fail with
ImportError when the engine's backend is unavailable (compiled extension,
optional dependency) — that is what makes "auto" fall through and what turns
into the install/build hint.
"""
from __future__ import annotations

import logging

from importlib import import_module
from typing import Any

logger = logging.getLogger(__name__)


class EngineRegistry:
    """name → lazily imported engine module, with aliases and an auto order."""

    def __init__(self, kind: str, package: str, modules: dict, aliases: dict | None = None,
                 hints: dict | None = None, default_hint: str = "", auto: list | None = None):
        self.kind = kind              # "parser" / "sparql" / "validation" — for messages
        self.package = package        # anchor for the relative module imports
        self.modules = dict(modules)  # engine name → relative module name
        self.aliases = dict(aliases or {})
        self.hints = dict(hints or {})       # engine name → install/build hint
        self.default_hint = default_hint
        self.auto = list(auto if auto is not None else self.modules)
        self.loaded: dict[str, Any] = {}     # engine name → module (incl. custom engines)

    def register(self, name: str, module: Any) -> None:
        self.loaded[name] = module

    def load(self, name: str):
        if name in self.loaded:
            return self.loaded[name]
        module_name = self.modules.get(name)
        if module_name is None:
            raise ValueError(f"Unknown {self.kind} engine: {name}. Known: {', '.join(self.modules)}")
        try:
            self.loaded[name] = import_module(module_name, self.package)
        except ImportError as error:
            hint = self.hints.get(name, self.default_hint)
            raise ImportError(" ".join(filter(None, (
                f"{name} {self.kind} engine not available.", hint,
                f"Original error: {error}")))) from error
        return self.loaded[name]

    def get(self, name: str = "auto"):
        """Resolve *name* (aliases; "auto" = first importable) → (name, module)."""
        if name == "auto":
            for candidate in self.auto:
                try:
                    return candidate, self.load(candidate)
                except ImportError:
                    continue
            raise ImportError(" ".join(filter(None, (
                f"no {self.kind} engine available (tried: {', '.join(self.auto)}).",
                self.default_hint))))
        resolved = self.aliases.get(name, name)
        logger.debug("%s engine: %s", self.kind, resolved)
        return resolved, self.load(resolved)
