// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
// Dynamic playlists on the phone: the same rules as
// file-tags/dynamic_playlists.py (match all/any, nested groups, text / number /
// date rules, random or sorted, "prefer" file types), so a list that has been
// played down is topped up here instead of waiting for the next sync.  The
// desktop stays the master: its own refill replaces these entries at the sync.
#pragma once
#include <QHash>
#include <QJsonObject>
#include <QList>
#include <QSet>
#include <QString>
#include <QVector>

// The tracks the rules select (never a duplicate, never one of exclude), in
// order, at most limit of them; the same artist is not chosen twice in a row
// while another is left (not for a list in the order of the artist or the
// album).
QVector<qint64> selectTracks(const QString &dbPath, const QJsonObject &rules,
                             const QSet<qint64> &exclude, int limit,
                             QString *error = nullptr);

// Top up every dynamic playlist that has fewer visible entries than the
// configured length (meta dynamic_length, 20 by default): new tracks go at the
// end; entries already on the list (marked played or not) and the playlist's
// "played" set are never chosen; the first new track is of another artist than
// the last one on the list when there is one.  Returns how many playlists
// changed; takes dbLock() itself.
int topUpDynamicPlaylists(const QString &dbPath, QString *problem = nullptr);

// Dynamic playlists with no visible entry have run out of songs: start each
// of them again by forgetting its entries and its "played" set, then top them
// up.  Returns the ids of the dynamic playlists that are still empty after it
// (the rules select nothing that may be chosen: they are drained).  Takes
// dbLock() itself.
QList<qint64> regenerateEmptyPlaylists(const QString &dbPath,
                                       QString *problem = nullptr);

// How many library tracks (duplicates excluded) the rules of a dynamic playlist
// select; -1 if it has none or the database cannot be read.  Takes no lock
// itself.
int countMatches(const QString &dbPath, qint64 playlistId);

// The same count for every dynamic playlist at once (the library is read once):
// playlist id -> matches.  Takes no lock.
QHash<qint64, int> countAllMatches(const QString &dbPath);

// The plays and the latest play of a whole song (every file with the same
// artist and title), by file id, for every file of the library.  Takes no
// lock.
struct SongStat {
    qint64 plays = 0;
    QString last;  // "yyyy-MM-ddTHH:mm:ssZ", empty if never played
    // these two belong to the file, not to the song: its BPM tag (0 = none)
    // and its loudness in LUFS (when measured)
    double bpm = 0;
    double loudness = 0;
    bool measured = false;
};
QHash<qint64, SongStat> songStats(const QString &dbPath);

// Why the tracks of a dynamic playlist were chosen: for each of the given track
// ids, the values of the fields its rules test (and sort by), such as "Last
// played: never  ·  Rating: not rated".  Empty for a playlist without rules.
// stats: the plays of every song, when the caller has them already (they are
// the longest part of this).  Takes no lock.
QHash<qint64, QString> explainTracks(
    const QString &dbPath, qint64 playlistId, const QList<qint64> &ids,
    const QHash<qint64, SongStat> *stats = nullptr);

// What the lists on the screen need after a play, from one pass over the
// library: how many tracks the rules of every dynamic playlist select (by
// playlist id), why the open playlist (openId, with the entries openIds)
// chose its entries, and the plays of every song.  Takes no lock.
struct DynamicInfo {
    QHash<qint64, int> matched;
    QHash<qint64, QString> why;
    QHash<qint64, SongStat> stats;
};
DynamicInfo dynamicInfo(const QString &dbPath, qint64 openId,
                        const QList<qint64> &openIds);

// The BPM tag of a file as a number; 0 if it has none or it is not a number.
// Takes no lock.
double trackBpm(const QString &dbPath, qint64 id);
