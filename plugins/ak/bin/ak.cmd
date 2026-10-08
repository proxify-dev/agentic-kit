@echo off
rem COPY of scripts/seam/launcher.cmd: edit that file, never this one (scripts/copies.py)
rem A plugin's command, named by this file: bin\<name>.cmd runs the <name> console script of the plugin it
rem sits in, in that plugin's own uv project env; twin of the POSIX launcher beside it.
rem Canonical in scripts/seam/; every plugin bin/ carries a copy (scripts/copies.py).
setlocal

rem UTF-8 for the CLI whatever the console code page (cp1252 cannot encode the glyphs it prints).
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem NAME = this file's name without .cmd; ROOT = the folder above this bin\ (no trailing backslash).
set "NAME=%~n0"
for %%I in ("%~dp0..") do set "ROOT=%%~fI"

where uv >nul 2>&1
if errorlevel 1 (
  echo %NAME%: needs uv - install it from https://docs.astral.sh/uv 1>&2
  exit /b 127
)

rem Plugin installs copy .venv without its python, leaving a corpse uv refuses to use.
rem Drop it so uv rebuilds a valid env (either layout counts as healthy).
if exist "%ROOT%\.venv" if not exist "%ROOT%\.venv\Scripts\python.exe" if not exist "%ROOT%\.venv\bin\python" rmdir /s /q "%ROOT%\.venv"

uv run --project "%ROOT%" --quiet %NAME% %*
exit /b %ERRORLEVEL%
