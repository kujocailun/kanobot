@echo off
cd /d "%~dp0"
if not exist "server.conf" (
    echo [ERROR] server.conf not found!
    echo Create server.conf with your server IP on the first line.
    pause
    exit /b
)
set /p SERVER_IP=<server.conf

echo === Uploading all files ===
scp *.py requirements.txt restart.sh songdb\maidata.json root@%SERVER_IP%:/root/kanobot/
if %errorlevel% neq 0 (
    echo Upload failed!
    pause
    exit /b
)
echo === Restarting bot ===
ssh root@%SERVER_IP% "mkdir -p /root/kanobot/songdb/cover && mv /root/kanobot/maidata.json /root/kanobot/songdb/ && bash /root/kanobot/restart.sh"
echo === Done! ===
pause
