#!/bin/bash
exec /opt/tapo-web/venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8555 --app-dir /opt/tapo-web
