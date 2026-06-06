#!/bin/bash
cd /root/kanobot
if [ -f /tmp/kanobot.pid ]; then
    kill $(cat /tmp/kanobot.pid) 2>/dev/null
    sleep 1
fi
nohup venv/bin/python main.py > kanobot.log 2>&1 &
echo $! > /tmp/kanobot.pid
echo "kanobot started, pid=$(cat /tmp/kanobot.pid)"
