# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Writes buildstamp.h, which names the build (the time and the source's git
# revision) so that the app can show and log which build is running.  Run at
# every build: cmake -DSOURCE=<dir> -DOUT=<header> -P stamp.cmake
string(TIMESTAMP now "%Y-%m-%d %H:%M:%S")
execute_process(
    COMMAND git describe --always --dirty
    WORKING_DIRECTORY ${SOURCE}
    OUTPUT_VARIABLE revision
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET)
if(revision STREQUAL "")
    set(revision "unknown")
endif()
file(WRITE ${OUT} "#pragma once\n#define MILKCRATE_BUILD \"${now} ${revision}\"\n")
