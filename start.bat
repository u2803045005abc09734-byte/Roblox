@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10+ wird benoetigt: https://www.python.org/downloads/ & pause & exit /b 1)
python -c "import PySide6" 2>nul || python -m pip install -r requirements.txt
python app.py
