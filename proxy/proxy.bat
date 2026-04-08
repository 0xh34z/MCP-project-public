@echo off
setlocal
set PROXY_PORT=1080
set TARGET_HOST=10.0.30.10
set SSH_KEY=%USERPROFILE%\.ssh\id_ed25519

title SSH Tunnel Manager - %TARGET_HOST%
echo ==================================================
echo [1/3] SSH Tunnel naar %TARGET_HOST% opstarten...
echo ==================================================

:: Start tunnel en sla PID op
start /b "" ssh -D %PROXY_PORT% -N -o "ExitOnForwardFailure=yes" -o "ServerAliveInterval=30" -o "ServerAliveCountMax=3" root@%TARGET_HOST%

:: Wacht langer en check meerdere keren (max 10s)
echo.
echo [2/3] Verbinding controleren...
set /a TRIES=0

:CHECK_LOOP
timeout /t 2 /nobreak >nul
netstat -ano | findstr ":%PROXY_PORT% " | findstr "LISTENING" >nul
if %errorlevel% equ 0 goto SUCCESS

set /a TRIES+=1
if %TRIES% lss 5 goto CHECK_LOOP

:: Na 5 pogingen = gefaald
echo.
echo [3/3] STATUS: FOUTMELDING
echo --------------------------------------------------
echo  - De tunnel kon NIET worden gestart.
echo  - Check: Zit je op de VPN van school?
echo  - Check: Staat je Proxmox-host %TARGET_HOST% aan?
echo  - Check: Is SSH beschikbaar op poort 22?
echo --------------------------------------------------
echo.
pause >nul
goto END

:SUCCESS
echo.
echo [3/3] STATUS: SUCCESS!
echo --------------------------------------------------
echo  - Tunnel ACTIEF op poort %PROXY_PORT%
echo  - FoxyProxy SOCKS5: 127.0.0.1:%PROXY_PORT%
echo  - Toegang tot: OPNsense en vmbr1 (192.168.1.x)
echo --------------------------------------------------
echo  LAAT DIT VENSTER OPEN TIJDENS HET WERKEN.
echo --------------------------------------------------
echo.
echo Druk op een toets om de tunnel te STOPPEN...
pause >nul

:END
:: Stop alle SSH tunnels naar deze host
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PROXY_PORT% " ^| findstr "LISTENING"') do (
    taskkill /PID %%p /F >nul 2>&1
)
echo Tunnel gestopt.