"""Shared engine registry — one dispatch pattern for every subsystem.

One pattern in one place: engine name → module imported on first use, alias
resolution, "auto" preference order, availability probing, and an actionable
error when an engine (or every auto candidate) is unavailable. Each package
instantiates EngineRegistry with its engines and keeps thin public
register_engine / get_engine wrappers, so the per-package API is unchanged.

Selection is eager, loading is lazy: registries are constructed at import
time and probe availability with importlib.util.find_spec (microseconds,
imports nothing), so what "auto" resolves to is fixed and inspectable from
the moment ``import triplets`` returns — see :func:`engines`. The chosen
module is only actually imported on first use.

The registry contract for engine modules: importing the module must fail with
ImportError when the engine's backend is unavailable (compiled extension,
optional dependency) — that is what makes "auto" fall through and what turns
into the install/build hint. ``requires`` lists find_spec probe targets beyond
the module itself (optional dependencies the module imports at its top).

``policy`` separates the two kinds of subsystems: ``"auto"`` registries pick
the fastest available engine because their results are flavor-independent
(parsed files, exported bytes, query results, violation reports); ``"input"``
registries hold engines that are each bound to their input's flavor (tools,
csv) — those cannot be overridden globally, only chosen per call or by
passing input in the desired flavor.

Precedence for "auto" resolution: per-call ``engine=`` (any explicit name
bypasses everything) > :func:`set_engine` override > probe order. ``set_engine``
is a process-global startup-time control, not a per-thread one; concurrent
code that needs a specific engine should pass ``engine=`` per call.
"""
from __future__ import annotations

import logging

from importlib import import_module
from importlib.util import find_spec
from typing import Any

logger = logging.getLogger(__name__)

REGISTRIES: dict[str, EngineRegistry] = {}   # kind → registry, self-registered


class EngineRegistry:
    """name → lazily imported engine module, with aliases and an auto order."""

    def __init__(self, kind: str, package: str, modules: dict, aliases: dict | None = None,
                 hints: dict | None = None, default_hint: str = "", auto: list | None = None,
                 requires: dict | None = None, policy: str = "auto"):
        self.kind = kind              # "parser_cimxml" / "sparql" / … — for messages and engines();
                                      # format-specific kinds carry a role prefix (parser_/exporter_)
        self.package = package        # anchor for the relative module imports
        self.modules = dict(modules)  # engine name → relative module name
        self.aliases = dict(aliases or {})
        self.hints = dict(hints or {})       # engine name → install/build hint
        self.default_hint = default_hint
        self.auto = list(auto if auto is not None else self.modules)
        self.requires = dict(requires or {})  # engine name → extra find_spec targets
        self.policy = policy                  # "auto" (fastest wins) | "input" (bound to input flavor)
        self.override = None                  # set_engine() target (resolved name)
        self.loaded: dict[str, Any] = {}      # engine name → module (incl. custom engines)
        self._probed: dict[str, bool] = {}    # find_spec target → exists (memo)
        REGISTRIES[kind] = self

    def register(self, name: str, module: Any) -> None:
        self.loaded[name] = module

    def _spec_exists(self, target: str) -> bool:
        if target not in self._probed:
            try:
                self._probed[target] = find_spec(target, self.package) is not None
            except (ImportError, ValueError):
                self._probed[target] = False
        return self._probed[target]

    def available(self, name: str) -> bool:
        """find_spec probe — never imports. True for custom-registered engines."""
        name = self.aliases.get(name, name)
        if name in self.loaded:
            return True
        if name not in self.modules:
            return False
        targets = (self.modules[name], *self.requires.get(name, ()))
        return all(self._spec_exists(t) for t in targets)

    def available_engines(self) -> list[str]:
        return [n for n in {**self.modules, **dict.fromkeys(self.loaded)} if self.available(n)]

    @property
    def selected(self) -> str | None:
        """What "auto" resolves to right now (override, else first available auto candidate)."""
        return self.override or next((c for c in self.auto if self.available(c)), None)

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
        """Resolve *name* (aliases; "auto" = selected engine) → (name, module)."""
        if name == "auto":
            if self.override:
                return self.override, self.load(self.override)
            for candidate in self.auto:
                if not self.available(candidate):
                    continue
                try:
                    return candidate, self.load(candidate)
                except ImportError:                  # probed available but broken build
                    logger.warning("%s engine %s found but failed to import; trying next",
                                   self.kind, candidate)
                    self._probed[self.modules[candidate]] = False
            raise ImportError(" ".join(filter(None, (
                f"no {self.kind} engine available (tried: {', '.join(self.auto)}).",
                self.default_hint))))
        resolved = self.aliases.get(name, name)
        logger.debug("%s engine: %s", self.kind, resolved)
        return resolved, self.load(resolved)

    def set(self, name: str | None) -> None:
        """Set (or clear, with None/"auto") the engine "auto" resolves to."""
        if self.policy != "auto":
            raise ValueError(f"the {self.kind} engine follows the input flavor and cannot be "
                             f"overridden; pass input in the desired engine's native flavor instead")
        if name in (None, "auto"):
            self.override = None
            return
        resolved = self.aliases.get(name, name)
        self.load(resolved)          # fail fast: unknown → ValueError, unavailable → ImportError + hint
        self.override = resolved

    def info(self) -> dict:
        """One engines() row.

        ``policy="input"`` kinds have no global engine — the input object's
        flavor decides per call — so ``engine`` is None and ``source`` is
        ``"input"`` instead of pretending an auto pick exists.
        """
        if self.policy == "input":
            engine, source = None, "input"
        else:
            engine = self.selected
            source = "set_engine" if self.override else "auto"
        return {"engine": engine,
                "source": source,
                "policy": self.policy,
                "auto_order": list(self.auto),
                "available": self.available_engines(),
                "unavailable": [n for n in self.modules if not self.available(n)],
                "aliases": dict(self.aliases)}


def engines() -> dict[str, dict]:
    """What each subsystem's "auto" resolves to, plus the available alternatives.

    Returns {kind: {engine, source, policy, auto_order, available, unavailable,
    aliases}}. ``policy="input"`` subsystems (tools, exporter_csv) follow the
    input object's flavor per call, so they report ``engine: None`` and
    ``source: "input"`` — there is no global pick to show.
    """
    return {kind: registry.info() for kind, registry in sorted(REGISTRIES.items())}


def set_engine(**engines_by_kind) -> None:
    """Global engine override per subsystem, e.g. ``set_engine(parser_cimxml="python_lxml_arrow",
    sparql="rdflib")``. ``None`` or ``"auto"`` restores auto-selection.

    Per-call ``engine=`` still wins over this. Process-global and intended for
    startup configuration; loads the engine eagerly so a bad name or missing
    dependency fails here, not at first use. Note per-call capability
    constraints still apply (e.g. cimxml ``datatypes=True`` requires the
    python_lxml engine regardless of the override).
    """
    unknown = set(engines_by_kind) - set(REGISTRIES)
    if unknown:
        raise ValueError(f"unknown subsystem(s) {sorted(unknown)}; known: {sorted(REGISTRIES)}")
    for kind, name in engines_by_kind.items():
        REGISTRIES[kind].set(name)
