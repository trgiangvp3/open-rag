@echo off
setlocal

REM ================================================================
REM  OpenRAG - Build Frontend + Web Deploy to IIS
REM  Usage: scripts\webdeploy.cmd [password]
REM ================================================================

set ROOT=%~dp0..
set PASSWORD=%~1

echo.
echo  ==================================================
echo   OpenRAG - Web Deploy
echo  ==================================================
echo.

REM -- 1. Build Frontend --
echo  [1/3] Build frontend...
cd /d "%ROOT%\frontend"
call npm install --prefer-offline
if errorlevel 1 (
    echo  FAILED: npm install
    exit /b 1
)
call npm run build
if errorlevel 1 (
    echo  FAILED: npm run build
    exit /b 1
)
echo  OK: Frontend built

REM -- 2. Create logs dir placeholder --
echo  [2/3] Preparing...
cd /d "%ROOT%\OpenRAG.Api"
if not exist "logs" mkdir logs
if not exist "data" mkdir data
if not exist "data\.gitkeep" echo. > "data\.gitkeep"

REM -- 3. Web Deploy --
echo  [3/3] Publishing via Web Deploy...
cd /d "%ROOT%"

if "%PASSWORD%"=="" (
    echo.
    echo  Nhap mat khau Web Deploy:
    set /p PASSWORD="  Password: "
)

dotnet publish OpenRAG.Api/OpenRAG.Api.csproj ^
    /p:PublishProfile=IIS-WebDeploy ^
    /p:Password=%PASSWORD% ^
    -c Release

if errorlevel 1 (
    echo.
    echo  FAILED: Web Deploy that bai!
    echo.
    echo  Kiem tra:
    echo    - Web Deploy da cai tren server chua?
    echo    - Port 12178 co mo trong firewall?
    echo    - ASP.NET Core 8.0 Hosting Bundle da cai?
    echo    - WebSocket Protocol da bat trong IIS?
    exit /b 1
)

echo.
echo  ==================================================
echo   Deploy thanh cong!
echo  ==================================================
echo.
echo   URL: http://vanban.fasoft.vn
echo.

pause
