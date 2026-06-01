@echo off
cd /d "%~dp0"
python run_tests.py --dry-run %*
pause
