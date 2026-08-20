

@echo off
cd /d "%~dp0"

SET logfile=job_scrapper.log

if "%~1"=="__main__" goto :main
echo Logging to %logfile%
if exist "%logfile%" powershell -NoProfile -Command "if ((Get-Item '%logfile%').Length -gt 1MB) { (Get-Content '%logfile%' -Tail 2000) | Set-Content '%logfile%' }"
cmd /c "%~f0" __main__ 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%logfile%' -Append"
exit /b %errorlevel%

:main
echo ============================================================
echo python %0 %1 
echo Run started: %date% %time%
echo -------------------------------------------------------------

echo Checking dependencies (first run may take a minute for installing packages)...
python -m pip install --quiet --disable-pip-version-check requirements.txt

echo Launching converter...
python -u "%~dp0\run.py" --enrich-workers 40

if errorlevel 1 (
    echo.
    echo Something went wrong. Make sure Python is installed and on your PATH.
    pause
)
