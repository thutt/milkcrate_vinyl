@echo off
rem Copyright (c) 2026 Logic Magicians Software
rem SPDX-License-Identifier: GPL-3.0-only
rem This program is free software: you can redistribute it and/or modify it
rem under the terms of the GNU General Public License version 3 as published
rem by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
rem see the file LICENSE for the license text.
rem
rem Creates the Python virtual environment at %MILKCRATEVINYL_VENV% and
rem installs desktop\requirements.txt into it.  The other scripts run this
rem when the environment is missing; run it by hand to repair or update one.
rem Needs Python 3 (python.org) and network access.
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "HERE=%~dp0"
for %%i in ("%HERE%..") do set "ROOT=%%~fi\"
if not defined MILKCRATEVINYL_VENV (
    echo error: MILKCRATEVINYL_VENV is not set: it says where the Python
    echo virtual environment is.
    echo   Set it in this Command Prompt:
    echo       %HERE%setup --venv DIR --database FILE
    exit /b 1
)
if not exist "%ROOT%desktop\requirements.txt" (
    echo error: %ROOT%desktop\requirements.txt is missing.
    exit /b 1
)
if exist "%MILKCRATEVINYL_VENV%\Scripts\python.exe" (
    echo %MILKCRATEVINYL_VENV% already exists;
    echo installing the requirements into it.
    goto mv_install
)
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo error: Python 3 was not found.  Install it from https://python.org
    echo ^(tick "Add python.exe to PATH"^) and open a new Command Prompt.
    exit /b 1
)
%PY% -m venv "%MILKCRATEVINYL_VENV%"
if errorlevel 1 (
    echo error: the virtual environment could not be created.
    exit /b 1
)
:mv_install
"%MILKCRATEVINYL_VENV%\Scripts\python.exe" -B -m pip install -r "%ROOT%desktop\requirements.txt"
if errorlevel 1 (
    echo error: the requirements could not be installed.
    exit /b 1
)
echo The virtual environment at %MILKCRATEVINYL_VENV% is ready.
exit /b 0
