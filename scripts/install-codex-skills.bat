@echo off
setlocal

for %%I in ("%~dp0..") do set "ROOT=%%~fI"

if defined CODEX_HOME (
  set "DEST=%CODEX_HOME%\skills"
) else (
  set "DEST=%USERPROFILE%\.codex\skills"
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)

%PY% "%ROOT%\scripts\install-codex-skills.py" %*
exit /b %ERRORLEVEL%
