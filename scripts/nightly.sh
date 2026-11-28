#!/bin/bash
# Nightly (cron, 4:00 AM local): grade yesterday from free box scores (0 BettingPros requests), grade halftime flags,
# adapt the Sam score weights to results, write the weekly review, and rebuild yesterday's note and dashboard.
# No paid API calls. Publishing the dashboard to claude.ai happens in the next Claude session.
cd "$(dirname "$0")/.." || exit 1
PY=/Library/Frameworks/Python.framework/Versions/3.11/bin/python3
D=${1:-$(TZ=America/New_York date -v-1d +%Y-%m-%d)}
{
  echo "=== nightly $D ($(date)) ==="
  "$PY" bp_props.py --grade "$D" --max-requests 0
  "$PY" bp_halftime.py --grade "$D"
  "$PY" -m nbaprops.rules.livepastes --grade "$D"
  "$PY" -m nbaprops.pricing.samfit --adapt --date "$D"
  "$PY" bp_props.py --weekly --date "$D" > /dev/null && echo "weekly_$D.md written"
  if [ -f "bp_props_$D.csv" ]; then "$PY" bp_props.py --from-csv --date "$D" | tail -3; fi
  T=$(TZ=America/New_York date -v+1d -j -f %Y-%m-%d "$D" +%Y-%m-%d)          # today's slip: last night's results on top
  if [ -f "bp_props_$T.csv" ]; then "$PY" bp_props.py --from-csv --date "$T" | tail -1; fi
  "$PY" bp_site.py --publish            # public site: build + secret check; pushes only if config.SITE_PUBLISH
  echo "=== done $(date) ==="
} >> "logs/nightly.log" 2>&1
