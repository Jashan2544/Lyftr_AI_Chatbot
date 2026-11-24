#!/bin/sh
# Writes runtime config so one image works against any backend URL.
set -e
cat > /usr/share/nginx/html/env.js <<JS
window.__ENV__ = { API_URL: "${API_URL:-http://localhost:8000}" };
JS
