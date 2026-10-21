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
#include <QVector>

struct Track {
    qint64 id = 0;
    QString path;  // relative to the music root
    QString title, artist, album,
        genre;       // genre: all of the track's genres, for the search only
    int number = 0;  // track number, 0 = none
    double loudness = 0;  // LUFS
    bool measured = false;
    QString key;  // search text: lower case words separated by single spaces
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
};

class TrackModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)
    Q_PROPERTY(int total READ total NOTIFY countChanged)
public:
    enum Role { IdRole = Qt::UserRole + 1, TitleRole, ArtistRole, AlbumRole };
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
    void setSort(const QString &key);  // "artist", "title", "album" or "path"
signals:
    void countChanged();

private:
    void rebuild();
    QVector<Track> m_all;
    QHash<qint64, int> m_index;  // id -> position in m_all
    QVector<int> m_view;         // positions in m_all that are shown, in order
    QStringList m_words;
    QString m_sort = "artist";
};

// The SD card (exFAT) cannot hold some names, so the library is copied to it
// with cleaned names (tools/copy_library.py, clean_path: keep the two
// identical): each of * ? : " < > | \ and control characters becomes _, and
// trailing dots and spaces of a file or folder name become _.
QString cleanPath(const QString &relative);

QString searchKey(const QString &text);  // lower case, punctuation as spaces
