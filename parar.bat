@echo off
echo ============================================
echo   BotBinance - Parando containers Docker
echo ============================================
cd /d %~dp0
docker-compose down
echo.
echo Containers parados.
pause
