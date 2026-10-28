// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
// The library as a list: every track that is not a duplicate, with the tags the
// list shows, filtered by a search text and ordered by one of a few sort keys.
// Read from the same tags.db the desktop programs use.
#pragma once
#include <QAbstractListModel>
#include <QHash>
#include <QList>
#include <QString>
#include <QStringList>
#include <QVector>

#include "dynamic.h"

struct Track {
    qint64 id = 0;
    QString path;  // relative to the music root
    QString title, artist, album,
        genre;       // genre: all of the track's genres, for the search only
    QStringList genres;  // the same, one by one, for the automatic equalizer
    int number = 0;  // track number, 0 = none
    double loudness = 0;  // LUFS
    bool measured = false;
    int rating = -1;  // 0 to 5 stars, -1 = not rated
    QString key;  // search text: lower case words separated by single spaces
    QString keyTitle, keyArtist, keyAlbum, keyGenre,
        keyPath;  // the same for one field, for "genre:rock"
    QString sortArtist, sortTitle, sortAlbum;
};

// What the player needs to play a track (a copy, so the playback thread never
// touches the model).
struct QueueItem {
    qint64 id = 0;
    QString path, title, artist, album;
    double loudness = 0;
    bool measured = false;
    qint64 pos = -1;  // place in the playlist it came from
    int rating = -1;  // 0 to 5 stars, -1 = not rated (for the lists)
    QString detail;   // why a dynamic playlist chose it (for the lists)
    QStringList genres;  // for the automatic equalizer: no database read when
                         // the song starts
};

class TrackModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)
    Q_PROPERTY(int total READ total NOTIFY countChanged)
public:
    enum Role {
        IdRole = Qt::UserRole + 1,
        TitleRole,
        ArtistRole,
        AlbumRole,
        RatingRole,
        DetailRole
    };
    explicit TrackModel(QObject *parent = nullptr)
        : QAbstractListModel(parent) {}

    int rowCount(const QModelIndex &parent = {}) const override {
        return parent.isValid() ? 0 : m_view.size();
    }
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    bool load(const QString &dbPath, QString *error);  // replaces all tracks
    int total() const { return m_all.size(); }
    const Track *byId(qint64 id) const;
    const Track *atRow(int row) const {
        return row >= 0 && row < m_view.size() ? &m_all[m_view[row]] : nullptr;
    }
    int rowOf(qint64 id) const;  // row in the current view, -1 if not shown
    QVector<QueueItem> viewItems() const;  // the list as shown, for a queue

    void setQuery(const QString &text);
    void setRating(qint64 id, int rating);  // a row shows it at once
    // the plays and last play of every song, by file id; a row shows them
    // when the list asks for its text, so only the rows on screen cost time
    void setStats(const QHash<qint64, SongStat> &stats);
    QHash<qint64, SongStat> stats() const { return m_stats; }
    void setSort(const QString &key);  // "artist", "title", "album" or "path"
signals:
    void countChanged();

private:
    struct Term {
        int field;  // 0 = title, artist, album and genre together; else Field
        QString text;  // searchKey of the word or phrase
        bool whole;    // =word: the whole word, not its start
        bool operator==(const Term &o) const {
            return field == o.field && text == o.text && whole == o.whole;
        }
    };
    void rebuild();
    QString statText(QHash<qint64, SongStat>::const_iterator it) const;
    static QVector<Term> parseQuery(const QString &text);
    static const QString &fieldKey(const Track &t, int field);
    QVector<Track> m_all;
    QHash<qint64, int> m_index;  // id -> position in m_all
    QVector<int> m_view;         // positions in m_all that are shown, in order
    QVector<Term> m_terms;
    QString m_sort = "artist";
    QHash<qint64, SongStat> m_stats;
};

// The SD card (exFAT) cannot hold some names, so the library is copied to it
// with cleaned names (tools/copy_library.py, clean_path: keep the two
// identical): each of * ? : " < > | \ and control characters becomes _, and
// trailing dots and spaces of a file or folder name become _.
QString cleanPath(const QString &relative);

QString searchKey(const QString &text);  // lower case, punctuation as spaces
