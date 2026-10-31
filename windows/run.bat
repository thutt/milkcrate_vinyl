@echo off
rem Copyright (c) 2026 Logic Magicians Software
rem SPDX-License-Identifier: GPL-3.0-only
rem This program is free software: you can redistribute it and/or modify it
rem under the terms of the GNU General Public License version 3 as published
rem by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
rem see the file LICENSE for the license text.
rem
rem Called by the wrapper scripts:  run PROGRAM OPTION ARGUMENTS...
rem   PROGRAM  the Python file, relative to the folder of the package
rem   OPTION   the option that names the database for the program (a wrapper
rem            uses the one it was given; else it adds OPTION with the
rem            database that setup recorded)
setlocal enabledelayedexpansion
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "HERE=%~dp0"
for %%i in ("%HERE%..") do set "ROOT=%%~fi\"
call "%HERE%require-venv.bat"
if errorlevel 1 exit /b 1
set "PROGRAM=%~1"
set "OPTION=%~2"
shift
shift
rem the remaining arguments, each in quotes; GIVEN says that one of them is
rem OPTION (as "OPTION value" or "OPTION=value"; -d for --database)
set "ARGS="
set "GIVEN="
:mv_next
if "%~1"=="" goto mv_run
set "ARG=%~1"
set "ARGS=!ARGS! "!ARG!""
for /f "delims==" %%k in ("!ARG!") do (
    if /i "%%k"=="%OPTION%" set "GIVEN=1"
    if /i "%%k"=="-d" if /i "%OPTION%"=="--database" set "GIVEN=1"
)
shift
goto mv_next
:mv_run
set "PYTHONPATH=%ROOT%desktop;%ROOT%android\tools"
if defined GIVEN (
    "%MILKCRATEVINYL_VENV%\Scripts\python.exe" -B "%ROOT%%PROGRAM%"!ARGS!
    exit /b !errorlevel!
)
if not defined MILKCRATEVINYL_DATABASE (
    echo error: no database: %OPTION% was not given and
    echo MILKCRATEVINYL_DATABASE is not set.
    echo   Give the database with %OPTION% FILE, or set it for this Command
    echo   Prompt:
    echo       %HERE%setup --venv DIR --database FILE
    exit /b 1
)
"%MILKCRATEVINYL_VENV%\Scripts\python.exe" -B "%ROOT%%PROGRAM%" %OPTION% "%MILKCRATEVINYL_DATABASE%"!ARGS!
exit /b !errorlevel!
