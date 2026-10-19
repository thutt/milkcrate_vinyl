// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
#include "trackmodel.h"

#include <QSqlDatabase>
#include <QSqlError>
#include <QSqlQuery>
#include <algorithm>

QString cleanPath(const QString &relative) {
    QStringList parts = relative.split('/');
    for (QString &part : parts) {
        for (QChar &c : part)
            if (c.unicode() < 0x20 || QStringLiteral("*?:\"<>|\\").contains(c))
                c = '_';
        int end = part.size();
        while (end > 0 && (part[end - 1] == '.' || part[end - 1] == ' ')) --end;
        for (int i = end; i < part.size(); ++i) part[i] = '_';
    }
    return parts.join('/');
}

QString searchKey(const QString &text) {
    QString out;
    out.reserve(text.size());
    bool space = true;
    for (QChar c : text.toLower()) {
        if (c.isLetterOrNumber()) {
            out += c;
            space = false;
        } else if (!space) {
            out += ' ';
            space = true;
        }
    }
    return out.trimmed();
}

QVariant TrackModel::data(const QModelIndex &index, int role) const {
    const Track *t = index.isValid() ? atRow(index.row()) : nullptr;
    if (!t) return {};
    switch (role) {
        case IdRole: return t->id;
        case TitleRole: return t->title;
        case ArtistRole: return t->artist;
        case AlbumRole: return t->album;
    }
    return {};
}

QHash<int, QByteArray> TrackModel::roleNames() const {
    return {{IdRole, "trackId"},
            {TitleRole, "title"},
            {ArtistRole, "artist"},
            {AlbumRole, "album"}};
}

bool TrackModel::load(const QString &dbPath, QString *error) {
    QVector<Track> all;
    QHash<qint64, int> index;
    {
        QSqlDatabase db = QSqlDatabase::addDatabase("QSQLITE", "tracks");
        db.setDatabaseName(dbPath);
        db.setConnectOptions("QSQLITE_OPEN_READONLY");
        if (!db.open()) {
            if (error)
                *error = "Cannot open the database: " + db.lastError().text();
            db.close();
            QSqlDatabase::removeDatabase("tracks");
            return false;
        }
        QSqlQuery q(db);
        q.setForwardOnly(true);
        bool ok = q.exec(
            "SELECT m.id, m.path, m.loudness, t.name, t.value FROM media_file "
            "m "
            "LEFT JOIN tag t ON t.file_id = m.id AND ((t.pos = 0 AND t.name IN "
            "('TITLE', 'ARTIST', 'ALBUM', 'TRACKNUMBER')) OR t.name = 'GENRE') "
            "WHERE m.duplicate_of IS NULL AND m.read_error IS NULL ORDER BY "
            "m.id");
        if (!ok) {
            if (error)
                *error = "Cannot read the database: " + q.lastError().text();
        } else {
            while (q.next()) {
                qint64 id = q.value(0).toLongLong();
                auto it = index.find(id);
                if (it == index.end()) {
                    Track t;
                    t.id = id;
                    t.path = q.value(1).toString();
                    t.measured = !q.value(2).isNull();
                    t.loudness = t.measured ? q.value(2).toDouble() : 0;
                    index.insert(id, all.size());
                    all.append(t);
                    it = index.find(id);
                }
                Track &t = all[it.value()];
                QString name = q.value(3).toString(),
                        value = q.value(4).toString();
                if (name == "TITLE")
                    t.title = value;
                else if (name == "ARTIST")
                    t.artist = value;
                else if (name == "ALBUM")
                    t.album = value;
                else if (name == "GENRE")
                    t.genre += (t.genre.isEmpty() ? "" : " ") +
                               value;  // a track can have several genres
                else if (name == "TRACKNUMBER")
                    t.number = value.toInt();  // "3/12" reads as 3
            }
        }
        q.finish();
        db.close();
        if (!ok) {
            QSqlDatabase::removeDatabase("tracks");
            return false;
        }
    }
    QSqlDatabase::removeDatabase("tracks");
    for (Track &t : all) {
        if (t.title.isEmpty()) {
            int slash = t.path.lastIndexOf('/'), dot = t.path.lastIndexOf('.');
            t.title = t.path.mid(slash + 1, dot > slash ? dot - slash - 1 : -1);
        }
        t.key =
            searchKey(t.title + ' ' + t.artist + ' ' + t.album + ' ' + t.genre);
        QString num = QString("%1").arg(t.number, 4, 10, QChar('0'));
        t.sortArtist = t.artist.toLower() + '\x01' + t.album.toLower() +
                       '\x01' + num + '\x01' + t.title.toLower();
        t.sortTitle = t.title.toLower() + '\x01' + t.artist.toLower();
        t.sortAlbum =
            t.album.toLower() + '\x01' + num + '\x01' + t.title.toLower();
    }
    beginResetModel();
    m_all = all;
    m_index = index;
    rebuild();
    endResetModel();
    emit countChanged();
    return true;
}

const Track *TrackModel::byId(qint64 id) const {
    auto it = m_index.find(id);
    return it == m_index.end() ? nullptr : &m_all[it.value()];
}

int TrackModel::rowOf(qint64 id) const {
    auto it = m_index.find(id);
    if (it == m_index.end()) return -1;
    for (int row = 0; row < m_view.size(); ++row)
        if (m_view[row] == it.value()) return row;
    return -1;
}

QVector<QueueItem> TrackModel::viewItems() const {
    QVector<QueueItem> items;
    items.reserve(m_view.size());
    for (int pos : m_view) {
        const Track &t = m_all[pos];
        items.append(
            {t.id, t.path, t.title, t.artist, t.album, t.loudness, t.measured});
    }
    return items;
}

void TrackModel::setQuery(const QString &text) {
    QStringList words = searchKey(text).split(' ', Qt::SkipEmptyParts);
    if (words == m_words) return;
    beginResetModel();
    m_words = words;
    rebuild();
    endResetModel();
    emit countChanged();
}

void TrackModel::setSort(const QString &key) {
    if (key == m_sort) return;
    beginResetModel();
    m_sort = key;
    rebuild();
    endResetModel();
}

// Every search word has to start a word of the track's title, artist, album or
// genre.
void TrackModel::rebuild() {
    m_view.clear();
    for (int i = 0; i < m_all.size(); ++i) {
        const QString padded = ' ' + m_all[i].key;
        bool all = true;
        for (const QString &w : m_words)
            if (!padded.contains(' ' + w)) {
                all = false;
                break;
            }
        if (all) m_view.append(i);
    }
    auto less = [this](auto field) {
        return [this, field](int a, int b) {
            return m_all[a].*field < m_all[b].*field;
        };
    };
    if (m_sort == "title")
        std::stable_sort(m_view.begin(), m_view.end(), less(&Track::sortTitle));
    else if (m_sort == "album")
        std::stable_sort(m_view.begin(), m_view.end(), less(&Track::sortAlbum));
    else if (m_sort == "path")
        std::stable_sort(m_view.begin(), m_view.end(), less(&Track::path));
    else
        std::stable_sort(m_view.begin(), m_view.end(),
                         less(&Track::sortArtist));
}
