@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 价值投资工作台
cd /d "%~dp0"

set "PORT=%STOCKANALYZE_PORT%"
if "%PORT%"=="" set "PORT=8000"

echo ==========================================
echo   价值投资工作台 启动器
echo ==========================================
echo.

rem ---------- 1. 检查 Python ----------
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python。请先安装 Python 3.10 或更高版本，并勾选 "Add Python to PATH"。
    echo        下载地址：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do echo [1/3] Python 环境：%%v

rem ---------- 2. 检查依赖 ----------
python -c "import fastapi, uvicorn, jinja2, multipart, openpyxl, pdfplumber, requests, httpx" >nul 2>&1
if errorlevel 1 (
    echo [2/3] 缺少运行依赖，开始安装（首次可能需要几分钟）...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败。可尝试使用国内镜像后重试：
        echo        python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        echo.
        pause
        exit /b 1
    )
    echo       依赖安装完成。
) else (
    echo [2/3] 运行依赖已就绪。
)

rem ---------- 3. 检查端口是否已被本项目占用 ----------
set "OWNER="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue ^| Select-Object -First 1 -ExpandProperty OwningProcess)"`) do set "OWNER=%%p"

if not defined OWNER goto START_SERVER

echo [3/3] 端口 %PORT% 已被进程 %OWNER% 占用，正在确认是否为本项目服务...
set "IS_OURS=0"
powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%OWNER%' -ErrorAction SilentlyContinue; if($p -and $p.CommandLine -match 'app\.cli.*serve'){exit 0}else{exit 1}"
if not errorlevel 1 set "IS_OURS=1"

if "!IS_OURS!"=="0" (
    powershell -NoProfile -Command "try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:%PORT%/'; if($r.Content -match '价值投资工作台'){exit 0}}catch{}; exit 1"
    if not errorlevel 1 set "IS_OURS=1"
)

if "!IS_OURS!"=="1" (
    echo       确认是本项目服务，已在运行，不再重复启动。
    echo       本机访问：http://127.0.0.1:%PORT%
    start "" "http://127.0.0.1:%PORT%"
    echo.
    echo       如需停止：关闭正在运行的服务窗口，或在那个窗口按 Ctrl+C。
    echo       本窗口不会结束其他程序。
    echo.
    pause
    exit /b 0
)

echo.
echo [错误] 端口 %PORT% 被其他程序（PID %OWNER%）占用，未启动服务。
echo        为避免误杀其他进程，本脚本不会自动结束它。
echo        请关闭该程序，或设置环境变量 STOCKANALYZE_PORT 换一个端口后重试。
echo.
pause
exit /b 1

:START_SERVER
echo [3/3] 端口 %PORT% 空闲。
echo.
echo 服务启动后：
echo   本机访问   http://127.0.0.1:%PORT%
echo   局域网访问 见下方启动日志中的提示地址
echo   停止服务   直接关闭本窗口或按 Ctrl+C（进程会随窗口退出）
echo.
python -m app.cli serve --host 0.0.0.0 --port %PORT%

echo.
echo 服务已退出。
pause
