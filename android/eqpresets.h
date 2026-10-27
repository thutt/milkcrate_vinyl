// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
// The equalizer presets, and the choice of a preset from a song's genres
// (the automatic equalizer).
#pragma once
#include <QList>
#include <QString>
#include <QStringList>
#include <QVector>

struct EqPreset {
    const char *name;
    double gains[5];  // dB: 60 Hz, 230 Hz, 910 Hz, 3.6 kHz, 14 kHz
};

extern const EqPreset eqPresets[];
extern const int eqPresetCount;

// The preset for a song with these genres, or nullptr when none of them has
// one (a genre such as "Workout" says nothing about the sound).  The genres
// are tried against the presets in a fixed order, the most specific first, so
// "Rock; Metal" gets Metal and "Workout; Rock" gets Rock.
const EqPreset *eqPresetForGenres(const QStringList &genres);

// A preset the user saved.  It is chosen for a song when one of the song's
// genres contains its name (capitals ignored), before the built-in rules are
// tried; of several that match, the one with the longest name wins.
struct CustomEq {
    QString name;
    QVector<double> gains;  // dB, five bands
};

// The index into customs of the preset for these genres, or -1.
int eqCustomForGenres(const QList<CustomEq> &customs,
                      const QStringList &genres);
