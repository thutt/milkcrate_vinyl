@echo off
rem Copyright (c) 2026 Logic Magicians Software
rem SPDX-License-Identifier: GPL-3.0-only
rem This program is free software: you can redistribute it and/or modify it
rem under the terms of the GNU General Public License version 3 as published
rem by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
rem see the file LICENSE for the license text.
rem
rem Run it in a Command Prompt (not by double-clicking: the settings would be
rem lost when its window closes), as
rem
rem     setup --venv DIR --database FILE [--save]
rem
rem   --venv DIR       the Python virtual environment (it need not exist yet:
rem                    the other scripts create it when they first need it)
rem   --database FILE  the library database the programs open (it need not
rem                    exist yet: initialize-database creates it)
rem   --save           also keeps both for every Command Prompt opened later
rem
rem Both are recorded in MILKCRATEVINYL_VENV and MILKCRATEVINYL_DATABASE.
rem Without arguments the current settings are shown.
set "MV_VENV="
set "MV_DB="
set "MV_SAVE="
if "%~1"=="" goto mv_show
:mv_next
if "%~1"=="" goto mv_check
if /i "%~1"=="--venv" (
    set "MV_VENV=%~2"
    shift
    shift
    goto mv_next
)
if /i "%~1"=="--database" (
    set "MV_DB=%~2"
    shift
    shift
    goto mv_next
)
if /i "%~1"=="--save" (
    set "MV_SAVE=1"
    shift
    goto mv_next
)
echo error: unknown argument: %~1
goto mv_usage

:mv_check
if not defined MV_VENV (
    echo error: --venv is required.
    goto mv_usage
)
if not defined MV_DB (
    echo error: --database is required.
    goto mv_usage
)
for %%i in ("%MV_VENV%") do set "MV_VENV=%%~fi"
for %%i in ("%MV_DB%") do set "MV_DB=%%~fi"
set "MILKCRATEVINYL_VENV=%MV_VENV%"
set "MILKCRATEVINYL_DATABASE=%MV_DB%"
if defined MV_SAVE (
    setx MILKCRATEVINYL_VENV "%MV_VENV%" >nul
    setx MILKCRATEVINYL_DATABASE "%MV_DB%" >nul
    echo Saved for the Command Prompts opened from now on.
)
set "MV_VENV="
set "MV_DB="
set "MV_SAVE="

:mv_show
if not defined MILKCRATEVINYL_VENV goto mv_usage
if not defined MILKCRATEVINYL_DATABASE goto mv_usage
if exist "%MILKCRATEVINYL_VENV%\Scripts\python.exe" (
    echo MILKCRATEVINYL_VENV=%MILKCRATEVINYL_VENV%
) else (
    echo MILKCRATEVINYL_VENV=%MILKCRATEVINYL_VENV% ^(not created yet: it is
    echo     created when a script first needs it^)
)
if exist "%MILKCRATEVINYL_DATABASE%" (
    echo MILKCRATEVINYL_DATABASE=%MILKCRATEVINYL_DATABASE%
) else (
    echo MILKCRATEVINYL_DATABASE=%MILKCRATEVINYL_DATABASE% ^(not created yet:
    echo     run initialize-database --root DIR^)
)
exit /b 0

:mv_usage
echo usage: setup --venv DIR --database FILE [--save]
echo   --venv DIR       the Python virtual environment
echo   --database FILE  the library database
echo   --save           keep both for Command Prompts opened later
echo   Both are required.
set "MV_VENV="
set "MV_DB="
set "MV_SAVE="
exit /b 1
