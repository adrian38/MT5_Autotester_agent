@echo off
cd /d "%~dp0"
python compile_and_backtest.py %*
pause
