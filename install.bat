@echo off
chcp 65001 >nul
echo Installing libraries...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Done. Now run make_video.bat
pause
