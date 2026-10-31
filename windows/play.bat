@echo off
rem Copyright (c) 2026 Logic Magicians Software
rem SPDX-License-Identifier: GPL-3.0-only
rem This program is free software: you can redistribute it and/or modify it
rem under the terms of the GNU General Public License version 3 as published
rem by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
rem see the file LICENSE for the license text.
rem
rem Starts the player.
call "%~dp0run.bat" desktop\player.py --database %*
exit /b %errorlevel%
