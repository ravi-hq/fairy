import importlib.util as _util
import os as _os

# Load the legacy runtimes module via its file path, bypassing the normal
# import system (which would resolve "agent_on_demand.runtimes" to this
# package and create a circular lookup).
_source = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "runtimes.py")
_spec = _util.spec_from_file_location("agent_on_demand._runtimes_legacy", _source)
_mod = _util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

AgentModel = _mod.AgentModel
MODEL_RUNTIME_MAP = _mod.MODEL_RUNTIME_MAP
RuntimeConfig = _mod.RuntimeConfig
RUNTIMES = _mod.RUNTIMES

del _util, _os, _source, _spec, _mod
