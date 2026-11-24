"""Entry point kept for existing commands; the code lives in nbaprops.sources.rotowire."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("nbaprops.sources.rotowire", run_name="__main__", alter_sys=True)
else:
    import importlib
    sys.modules[__name__] = importlib.import_module("nbaprops.sources.rotowire")
