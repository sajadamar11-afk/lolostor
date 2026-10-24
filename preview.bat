@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "EP=%~1"
if not "%EP%"=="" goto run
echo.
echo   Episodes:
dir /b /ad episodes
echo.
set /p NAME=  Type episode folder name (example: 02_maghara) then Enter: 
set "EP=episodes\%NAME%"
:run
python story_video_maker.py "%EP%" --preview
echo.
pause
