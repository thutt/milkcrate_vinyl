# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
cmake_minimum_required(VERSION 3.21)
project(MusicPlayer LANGUAGES CXX)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_BINARY_DIR)
    message(FATAL_ERROR "In-source builds are not allowed: the source "
        "directories are never written to.  Use scripts/build-for-android.")
endif()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.8 REQUIRED COMPONENTS
    Quick QuickControls2 Multimedia Sql Network)
qt_standard_project_setup(REQUIRES 6.8)

qt_add_executable(musicplayer
    main.cpp equalizer.cpp equalizer.h eqpresets.cpp eqpresets.h trackmodel.cpp trackmodel.h engine.cpp engine.h dblock.h
    dynamic.cpp dynamic.h sync.cpp sync.h playlists.cpp playlists.h
    player.cpp player.h)
qt_add_qml_module(musicplayer URI MusicPlayer VERSION 1.0 QML_FILES Main.qml)
target_link_libraries(musicplayer PRIVATE
    Qt6::Quick Qt6::QuickControls2 Qt6::Multimedia Qt6::Sql Qt6::Network)

set_target_properties(musicplayer PROPERTIES
    QT_ANDROID_PACKAGE_SOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/android
    QT_ANDROID_PACKAGE_NAME "org.musicplayer.player"
    QT_ANDROID_TARGET_SDK_VERSION 34
    QT_ANDROID_MIN_SDK_VERSION 26)
