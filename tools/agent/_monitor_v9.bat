@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title v9 training - 5s - closing does NOT stop training
cd /d E:\电脑涨价分析\tools\agent
E:\ai2026\Scripts\python.exe watch_training.py --log train-v9.log --follow --interval 5
echo.
echo [monitor exited, training still running]
pause