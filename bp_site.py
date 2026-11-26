#!/usr/bin/env python3
"""ScuseBET public site build (see nbaprops/output/site.py)."""
import runpy

if __name__ == "__main__":
    runpy.run_module("nbaprops.output.site", run_name="__main__", alter_sys=True)
