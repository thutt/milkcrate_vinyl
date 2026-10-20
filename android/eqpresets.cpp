// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
#include "eqpresets.h"

const EqPreset eqPresets[] = {
    {"Flat", {0, 0, 0, 0, 0}},
    {"Rock", {4, 2, -2, 3, 4}},
    {"Metal", {4, -2, -3, 4, 3}},
    {"Punk", {3, 0, -2, 4, 3}},
    {"Pop", {-1, 2, 3, 1, -1}},
    {"Hip-hop / R&B", {6, 4, 0, 1, 2}},
    {"Electronic / Dance", {5, 2, -1, 2, 4}},
    {"Jazz", {3, 2, -2, 2, 3}},
    {"Classical", {3, 1, -1, 2, 3}},
    {"Acoustic", {2, 2, 1, 3, 2}},
    {"Country", {2, 1, 1, 3, 2}},
    {"Vocal", {-2, -1, 3, 4, 1}},
    {"Bass boost", {7, 5, 1, 0, 0}},
    {"Treble boost", {0, 0, 0, 4, 7}},
    {"Loudness", {6, 3, 0, 2, 5}},
};
const int eqPresetCount = sizeof(eqPresets) / sizeof(eqPresets[0]);

namespace {
// A genre that contains one of the words (capitals ignored) selects the
// preset.  The rows are tried in this order, so a more specific style wins.
struct Rule {
    const char *preset;
    QStringList words;
};
const QList<Rule> &rules() {
    static const QList<Rule> list = {
        {"Metal", {"metal"}},
        {"Punk", {"punk"}},
        {"Hip-hop / R&B",
         {"hip hop", "hip-hop", "hiphop", "rap", "r&b", "rnb", "soul", "funk"}},
        {"Electronic / Dance",
         {"electronic", "dance", "techno", "house", "trance", "edm",
          "electronica", "synth"}},
        {"Rock", {"rock"}},
        {"Jazz", {"jazz", "big band", "swing", "bebop", "blues"}},
        {"Classical", {"classical", "orchestral", "symphon", "opera"}},
        {"Country", {"country", "bluegrass"}},
        {"Acoustic", {"acoustic", "folk", "celtic", "hawaiian", "singer"}},
        {"Pop", {"pop", "new wave", "80s", "disco"}},
        {"Vocal", {"audiobook", "spoken", "podcast", "speech"}},
    };
    return list;
}
}  // namespace

const EqPreset *eqPresetForGenres(const QStringList &genres) {
    for (const Rule &rule : rules())
        for (const QString &genre : genres)
            for (const QString &word : rule.words)
                if (genre.contains(word, Qt::CaseInsensitive))
                    for (int i = 0; i < eqPresetCount; ++i)
                        if (rule.preset == QString(eqPresets[i].name))
                            return &eqPresets[i];
    return nullptr;
}
