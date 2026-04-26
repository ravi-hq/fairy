"""Check whether a (runtime, model) pair is compatible.

Extracted from `views/agents.py` so the two-branch compatibility check
— provider-mismatch and runtime-allowlist — can be mutation-tested in
isolation. The caller resolves ``RUNTIMES[name]`` and ``MODELS[id]``
and passes the resolved objects in; this module is pure (object
attributes in, error message or None out).

Two failure modes:

  - **Provider mismatch**: the runtime's ``providers`` set doesn't
    include the model's ``provider``. E.g. claude (anthropic-only)
    asked to serve gpt-4 (openai). Most common drift: a new runtime
    added to the registry forgets to declare its providers, and every
    model is suddenly "incompatible".
  - **Runtime allowlist**: the model declares an explicit ``runtimes``
    frozenset and the asked runtime isn't in it. E.g. a future model
    that only works under one runtime's CLI. Most current models have
    ``runtimes=None`` (compatible with any runtime whose providers
    accept them).

The two error messages are part of the API contract — SDKs parse them
to surface human-readable errors. They embed ``runtime.name`` and
``model.id``, which are equivalent to the registry lookup keys today
(``RUNTIMES[name].name == name`` and ``MODELS[id].id == id``). That
invariant is pinned by ``tests/test_runtimes.py::test_every_runtime_has_non_empty_providers``
and ``tests/test_models_catalog.py::test_all_entries_have_provider_matching_id_prefix``;
if either pin breaks the message strings will silently change shape,
so don't relax them without also routing the lookup key through here
explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_on_demand.models_catalog import ModelDef
    from agent_on_demand.runtimes import Runtime


def check_runtime_model_compat(runtime: Runtime, model: ModelDef) -> str | None:
    """Return ``None`` if ``runtime`` can serve ``model``, or an
    error-message string explaining why not.

    The caller in views/agents.py turns a non-None return into a 422.
    """
    if model.provider not in runtime.providers:
        return (
            f"Runtime {runtime.name} cannot serve model {model.id}: "
            f"provider {model.provider} not in {sorted(runtime.providers)}"
        )
    if model.runtimes is not None and runtime.name not in model.runtimes:
        return f"Model {model.id} not supported on runtime {runtime.name}"
    return None
