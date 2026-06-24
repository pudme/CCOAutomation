@echo off
echo Rebuilding CCOA app images (backend + frontend)...
docker compose up -d --build backend frontend
echo Done. Open http://localhost:3000/dashboard
