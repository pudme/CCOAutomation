#!/bin/bash
set -e

echo "Starting Apprio Compliance Platform..."
docker compose up -d

echo "Waiting for backend to be ready..."
until curl -sf http://localhost:8010/health > /dev/null 2>&1; do
    echo "  waiting..."
    sleep 3
done

echo "Platform ready."

# Open browser (Mac)
if [[ "$OSTYPE" == "darwin"* ]]; then
    open http://localhost:3000/dashboard
fi

echo "Dashboard: http://localhost:3000/dashboard"
