// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
// Playlists, containers and sequences from tags.db: the list shown on the
// Playlists tab, and what to play for each.
//   playlist  (manual or dynamic): its entries, in order (a dynamic playlist
//   keeps its current list there too) container: a time limit and the entries
//   of its playlists, one after another sequence : containers played one after
//   another, each one a section with its own clock
// Same rules as file-tags/containers.py.
#pragma once
#include <QAbstractListModel>
#include <QList>
#include <QString>
#include <QVector>

#include "trackmodel.h"

struct Section {
    QString name;
    qint64 limitMs = -1;           // -1 = no limit
    QString endMode = "fade";      // fade, cut or finish
    QString whenEmpty = "repeat";  // repeat or next
    QVector<QueueItem> items;
};

struct PlaylistInfo {
    qint64 id = 0;
    QString name, kind;  // kind: playlist, dynamic, container, sequence
    QString detail;
    bool holdsDynamic = false;  // dynamic, or a container or sequence that
                                // holds one: never shuffled
    int matched = -1;  // dynamic playlists: library tracks the rules select
                       // (-1: not counted yet)
};

// The tracks of one list (a playlist's entries), or the sections of a container
// or sequence.
QVector<QueueItem> playlistItems(const QString &dbPath, qint64 playlistId,
                                 QString *error = nullptr);
QVector<Section> playlistSections(const QString &dbPath, qint64 playlistId,
                                  QString *error = nullptr);
QList<PlaylistInfo> loadPlaylists(const QString &dbPath,
                                  QString *error = nullptr);
QString formatClock(qint64 ms);

class PlaylistModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)
    // How many groups the list has (sequences, containers, playlists): the
    // headings are shown only when there is more than one.
    Q_PROPERTY(int groups READ groups NOTIFY countChanged)
public:
    enum Role {
        IdRole = Qt::UserRole + 1,
        NameRole,
        KindRole,
        DetailRole,
        MatchedRole,
        GroupRole  // the heading it is under: a dynamic playlist is a playlist
    };
    int groups() const;
    explicit PlaylistModel(QObject *parent = nullptr)
        : QAbstractListModel(parent) {}
    int rowCount(const QModelIndex &parent = {}) const override {
        return parent.isValid() ? 0 : m_items.size();
    }
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    void
    setItems(const QList<PlaylistInfo> &items);  // counts already known stay
    void setMatched(const QHash<qint64, int> &counts);
    const PlaylistInfo *atRow(int row) const {
        return row >= 0 && row < m_items.size() ? &m_items[row] : nullptr;
    }
signals:
    void countChanged();

private:
    QList<PlaylistInfo> m_items;
};

// The entries of the playlist that is open on the screen.
class EntryModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Role {
        IdRole = Qt::UserRole + 1,
        TitleRole,
        ArtistRole,
        AlbumRole,
        RatingRole,
        DetailRole
    };
    explicit EntryModel(QObject *parent = nullptr)
        : QAbstractListModel(parent) {}
    int rowCount(const QModelIndex &parent = {}) const override {
        return parent.isValid() ? 0 : m_items.size();
    }
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    void setItems(const QVector<QueueItem> &items);
    void setRating(qint64 id, int rating);  // a row shows it at once
    // why a dynamic playlist chose each entry
    void setDetails(const QHash<qint64, QString> &why);
    // The plays of every song: the line under an entry of a playlist that is
    // not dynamic (a dynamic one has the reason it was chosen there).
    void setStats(const QHash<qint64, SongStat> &stats);
    void setDynamic(bool dynamic);  // the playlist that is open
    QString detailOf(qint64 id) const;  // the line of the song's row
    const QVector<QueueItem> &items() const { return m_items; }

private:
    QVector<QueueItem> m_items;
    QHash<qint64, SongStat> m_stats;
    bool m_dynamic = false;
};
