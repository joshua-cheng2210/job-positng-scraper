

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
echo %0 %*
echo Run started: %date% %time%
echo -------------------------------------------------------------

echo Checking dependencies (first run may take a minute for installing packages)...
python -m pip install --quiet --disable-pip-version-check -r "%~dp0\requirements.txt"

echo Launching converter...
python -u "%~dp0\run.py" --enrich-workers 40
set RUN_ERRORLEVEL=%errorlevel%

git add job_scrapper.log
git add data/postings.json data/ai_scores.json
git diff --cached --quiet
if not errorlevel 1 goto :skip_push
git commit -m "Update job data logs"
git push origin main
:skip_push

if %RUN_ERRORLEVEL% neq 0 (
    echo.
    echo Something went wrong. Make sure Python is installed and on your PATH.
    pause
)
