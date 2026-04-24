@echo off
echo ============================================
echo   BotBinance - Iniciando containers Docker
echo ============================================
cd /d %~dp0
docker-compose up -d --build
echo.
echo Containers iniciados!
echo Dashboard: http://localhost:5000
echo.
echo Para ver logs:  docker-compose logs -f
echo Para parar:     docker-compose down
pause
