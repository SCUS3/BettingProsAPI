"""Entry point kept for existing commands; the code lives in nbaprops.app.oddsapi_live."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("nbaprops.app.oddsapi_live", run_name="__main__", alter_sys=True)
else:
    import importlib
    sys.modules[__name__] = importlib.import_module("nbaprops.app.oddsapi_live")
