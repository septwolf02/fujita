@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  if "%~1"=="" (
    py -3 app.py --open
  ) else (
    py -3 app.py --open --workspace-root "%~1"
  )
) else (
  if "%~1"=="" (
    python app.py --open
  ) else (
    python app.py --open --workspace-root "%~1"
  )
)

