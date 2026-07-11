#!/bin/bash
set -a
source /root/tv-monitor/.env
set +a
cd /root/tv-monitor
/root/tv-monitor/venv/bin/python healthcheck.py >> /root/tv-monitor/logs/healthcheck.log 2>&1
