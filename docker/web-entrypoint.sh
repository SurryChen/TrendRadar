#!/bin/sh
set -eu
umask 077

if [ "$#" -eq 0 ]; then
    set -- serve --host 0.0.0.0 --port 18765 \
        --origin "${TREND_RADAR_ORIGIN:-http://127.0.0.1:18765}"
    case "${TREND_RADAR_ORIGIN:-}" in
        https://*) set -- "$@" --secure-cookie ;;
    esac
fi

exec python -m trendradar.web --data-dir "${TREND_RADAR_DATA_DIR:-/data}" "$@"
