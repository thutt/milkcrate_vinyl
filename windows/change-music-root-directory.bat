@echo off
rem Copyright (c) 2026 Logic Magicians Software
rem SPDX-License-Identifier: GPL-3.0-only
rem This program is free software: you can redistribute it and/or modify it
rem under the terms of the GNU General Public License version 3 as published
rem by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
rem see the file LICENSE for the license text.
rem
rem Changes the music root directory of the database.
call "%~dp0run.bat" desktop\change_music_root_directory.py --database %*
exit /b %errorlevel%
