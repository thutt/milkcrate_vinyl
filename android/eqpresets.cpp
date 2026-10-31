// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
#include "eqpresets.h"

#include <algorithm>
#include <cmath>

// Normal, Rock, Metal (Android's Heavy Metal), Pop, Hip-hop, Electronic /
// Dance, Jazz, Classical and Folk / Acoustic are the presets of Android's own
// equalizer (the AOSP LVM effect), which has five bands (drawn out to ten here, see eqFromFive); Punk,
// Country, Vocal, Bass boost, Treble boost and Loudness are ours.
const EqPreset eqPresets[] = {
    {"Flat", {0, 0, 0, 0, 0, 0, 0, 0, 0, 0}},
    {"Normal", {3, 3, 1, 0, 0, 0, 0, 0, 2, 3}},
    {"Rock", {5, 5, 4, 3, 1, -1, 1, 3, 4, 5}},
    {"Metal", {4, 4, 2, 1, 6, 9, 6, 3, 1, 0}},
    {"Punk", {3, 3, 1, 0, -1, -2, 1, 4, 3, 3}},
    {"Pop", {-1, -1, 1, 2, 4, 5, 3, 1, -1, -2}},
    {"Hip-hop / R&B", {5, 5, 4, 3, 1, 0, 1, 1, 2, 3}},
    {"Electronic / Dance", {6, 6, 3, 0, 1, 2, 3, 4, 2, 1}},
    {"Jazz", {4, 4, 3, 2, 0, -2, 0, 2, 4, 5}},
    {"Classical", {5, 5, 4, 3, 0, -2, 1, 4, 4, 4}},
    {"Folk / Acoustic", {3, 3, 1, 0, 0, 0, 1, 2, 0, -1}},
    {"Country", {2, 2, 1, 1, 1, 1, 2, 3, 2, 2}},
    {"Vocal", {-2, -2, -1, -1, 1, 3, 4, 4, 2, 1}},
    {"Bass boost", {7, 7, 6, 5, 3, 1, 0, 0, 0, 0}},
    {"Treble boost", {0, 0, 0, 0, 0, 0, 2, 4, 6, 7}},
    {"Loudness", {6, 6, 4, 3, 1, 0, 1, 2, 4, 5}},
};
const int eqPresetCount = sizeof(eqPresets) / sizeof(eqPresets[0]);

QVector<double> eqFromFive(const QVector<double> &five) {
    static const double from[5] = {60, 230, 910, 3600, 14000};
    static const double to[10] = {31, 62, 125, 250, 500,
                                  1000, 2000, 4000, 8000, 16000};
    QVector<double> out(10, 0.0);
    if (five.size() != 5) return out;
    for (int i = 0; i < 10; ++i) {
        const double x = std::log(to[i]);
        int k = 0;
        while (k < 3 && x > std::log(from[k + 1])) ++k;
        const double a = std::log(from[k]), b = std::log(from[k + 1]);
        const double t = std::clamp((x - a) / (b - a), 0.0, 1.0);
        out[i] = std::round(five[k] + t * (five[k + 1] - five[k]));
    }
    return out;
}

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
        {"Folk / Acoustic",
         {"acoustic", "folk", "celtic", "hawaiian", "singer"}},
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

int eqCustomForGenres(const QList<CustomEq> &customs,
                      const QStringList &genres) {
    int best = -1;
    for (int i = 0; i < customs.size(); ++i) {
        if (best >= 0 && customs[i].name.size() <= customs[best].name.size())
            continue;
        for (const QString &genre : genres)
            if (genre.contains(customs[i].name, Qt::CaseInsensitive)) {
                best = i;
                break;
            }
    }
    return best;
}
