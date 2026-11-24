"""Entry point kept for existing commands; the code lives in nbaprops.rules.halftime_backfill."""
import runpy
import sys

if __name__ == "__main__":
    runpy.run_module("nbaprops.rules.halftime_backfill", run_name="__main__", alter_sys=True)
else:
    import importlib
    sys.modules[__name__] = importlib.import_module("nbaprops.rules.halftime_backfill")
