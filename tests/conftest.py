"""Make `renglo` resolve to THIS checkout.

Two traps this defuses:

* The ambient interpreter has an editable install of renglo-lib registered
  against a DIFFERENT checkout on this machine, so a bare `import renglo`
  silently tests someone else's copy.
* noma's handler suite stubs the whole `renglo` package with MagicMocks in its
  own conftest; that conftest doesn't apply here, and these tests need the real
  class.
"""
import os
import sys

_RENGLO_LIB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RENGLO_LIB in sys.path:
    sys.path.remove(_RENGLO_LIB)
sys.path.insert(0, _RENGLO_LIB)
