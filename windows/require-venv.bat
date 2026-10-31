@echo off
rem Copyright (c) 2026 Logic Magicians Software
rem SPDX-License-Identifier: GPL-3.0-only
rem This program is free software: you can redistribute it and/or modify it
rem under the terms of the GNU General Public License version 3 as published
rem by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
rem see the file LICENSE for the license text.
rem
rem Called by the other scripts: stops them with a clear message unless
rem setup recorded the virtual environment; creates the environment the
rem first time it is needed.
if not defined MILKCRATEVINYL_VENV (
    echo error: MILKCRATEVINYL_VENV is not set: it says where the Python
    echo virtual environment is.
    echo   Set it in this Command Prompt:
    echo       %~dp0setup --venv DIR --database FILE
    exit /b 1
)
if not exist "%MILKCRATEVINYL_VENV%\Scripts\python.exe" (
    echo There is no virtual environment at %MILKCRATEVINYL_VENV%:
    echo creating it.  This happens only when it is missing;
    echo please wait.
    call "%~dp0create-venv.bat"
    if errorlevel 1 (
        echo error: the virtual environment could not be created.
        exit /b 1
    )
)
exit /b 0
