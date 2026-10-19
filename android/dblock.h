// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
// tags.db is written by the playback engine (a played track) and replaced by
// the sync server (the merged database from the desktop).  Whoever touches the
// file holds dbLock(); every write bumps dbGeneration(), so the server can tell
// that the database changed after the desktop took its copy (and the merge has
// to be done again).
#pragma once
#include <QMutex>
#include <atomic>

inline QMutex &dbLock() {
    static QMutex m;
    return m;
}
inline std::atomic<int> &dbGeneration() {
    static std::atomic<int> g{0};
    return g;
}
