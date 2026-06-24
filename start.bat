@echo off
echo Starting Apprio Compliance Platform...
docker compose up -d
echo Waiting for services to initialize...
timeout /t 15 /nobreak
start http://localhost:3000/dashboard
echo Platform started. Dashboard opening in browser.
