#!/usr/bin/env bash
# Quick-start script: runs the test suite first (fail fast if the
# environment is broken), then starts the backend and frontend.
set -e

echo "=== Checking dependencies ==="
command -v pdflatex >/dev/null 2>&1 || { echo "ERROR: pdflatex not found. See README.md setup section."; exit 1; }
command -v pdfinfo >/dev/null 2>&1 || { echo "ERROR: pdfinfo not found. See README.md setup section."; exit 1; }

echo "=== Installing Python dependencies ==="
pip install -r requirements.txt --break-system-packages 2>/dev/null || pip install -r requirements.txt

echo "=== Running test suite (79 tests) ==="
python3 -m pytest tests/ -q

echo "=== Starting backend on :5001 ==="
cd backend
python3 app.py &
BACKEND_PID=$!
cd ..

sleep 2

echo "=== Starting frontend on :8080 ==="
cd frontend
python3 -m http.server 8080 &
FRONTEND_PID=$!
cd ..

echo ""
echo "Ready. Open http://localhost:8080 in a browser."
echo "Press Ctrl+C to stop both servers."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
