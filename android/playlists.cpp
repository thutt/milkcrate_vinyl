// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
#include "playlists.h"
#include "dynamic.h"

#include <QSet>
#include <QSqlDatabase>
#include <algorithm>
#include <functional>
#include <QSqlError>
#include <QSqlQuery>
#include <QUuid>

namespace {

// A read-only connection that is closed again when this goes out of scope.
struct Connection {
    QString name = "playlists-" + QUuid::createUuid().toString(QUuid::Id128);
    bool ok = false;
    QString error;
    Connection(const QString &path) {
        QSqlDatabase db = QSqlDatabase::addDatabase("QSQLITE", name);
        db.setDatabaseName(path);
        db.setConnectOptions("QSQLITE_OPEN_READONLY;QSQLITE_BUSY_TIMEOUT=3000");
        ok = db.open();
        if (!ok) error = "Cannot open the database: " + db.lastError().text();
    }
    ~Connection() {
        {
            QSqlDatabase::database(name, false).close();
        }
        QSqlDatabase::removeDatabase(name);
    }
    QSqlDatabase db() const { return QSqlDatabase::database(name, false); }
};

// Entries the phone has played are marked removed_at (the desktop deletes them
// at the next sync): not shown, not played.
bool hasRemovedMark(QSqlDatabase db) {
    QSqlQuery q(db);
    q.exec("PRAGMA table_info(playlist_item)");
    while (q.next())
        if (q.value(1).toString() == "removed_at") return true;
    return false;
}

QVector<QueueItem> entriesOf(QSqlDatabase db, qint64 playlistId) {
    QVector<QueueItem> items;
    QSqlQuery q(db);
    q.prepare("SELECT m.id, m.path, m.loudness, "
              "(SELECT value FROM tag WHERE file_id = m.id AND name = 'TITLE' "
              "AND pos = 0), "
              "(SELECT value FROM tag WHERE file_id = m.id AND name = 'ARTIST' "
              "AND pos = 0), "
              "(SELECT value FROM tag WHERE file_id = m.id AND name = 'ALBUM' "
              "AND pos = 0), i.pos, m.rating, "
              "(SELECT group_concat(value, char(10)) FROM tag WHERE file_id = "
              "m.id AND name = 'GENRE') "
              "FROM playlist_item i JOIN media_file m ON m.id = i.file_id "
              "WHERE i.playlist_id = ?" +
              QString(hasRemovedMark(db) ? " AND i.removed_at IS NULL" : "") +
              " ORDER BY i.pos");
    q.addBindValue(playlistId);
    if (!q.exec()) return items;
    while (q.next()) {
        QueueItem t;
        t.id = q.value(0).toLongLong();
        t.path = q.value(1).toString();
        t.measured = !q.value(2).isNull();
        t.loudness = t.measured ? q.value(2).toDouble() : 0;
        t.title = q.value(3).toString();
        t.artist = q.value(4).toString();
        t.album = q.value(5).toString();
        t.pos = q.value(6).toLongLong();
        t.rating = q.value(7).isNull() ? -1 : q.value(7).toInt();
        if (!q.value(8).isNull())
            t.genres = q.value(8).toString().split('\n', Qt::SkipEmptyParts);
        if (t.title.isEmpty()) {
            int slash = t.path.lastIndexOf('/'), dot = t.path.lastIndexOf('.');
            t.title = t.path.mid(slash + 1, dot > slash ? dot - slash - 1 : -1);
        }
        items.append(t);
    }
    return items;
}

QList<qint64> membersOf(QSqlDatabase db, qint64 parentId) {
    QList<qint64> ids;
    QSqlQuery q(db);
    q.prepare("SELECT child_id FROM playlist_member WHERE parent_id = ? ORDER "
              "BY pos");
    q.addBindValue(parentId);
    if (q.exec())
        while (q.next()) ids.append(q.value(0).toLongLong());
    return ids;
}

QString kindOf(const QVariant &kind, const QVariant &rules) {
    if (!rules.isNull()) return "dynamic";
    QString k = kind.toString();
    return k == "container" || k == "sequence" ? k : "playlist";
}

QString limitText(const QVariant &limitSeconds) {
    return limitSeconds.isNull()
               ? QString("no limit")
               : formatClock(limitSeconds.toLongLong() * 1000);
}

}  // namespace

QString formatClock(qint64 ms) {
    qint64 s = qMax<qint64>(0, ms / 1000), h = s / 3600, m = s % 3600 / 60;
    return h ? QString("%1:%2:%3")
                   .arg(h)
                   .arg(m, 2, 10, QChar('0'))
                   .arg(s % 60, 2, 10, QChar('0'))
             : QString("%1:%2").arg(m).arg(s % 60, 2, 10, QChar('0'));
}

QVector<QueueItem> playlistItems(const QString &dbPath, qint64 playlistId,
                                 QString *error) {
    Connection c(dbPath);
    if (!c.ok) {
        if (error) *error = c.error;
        return {};
    }
    return entriesOf(c.db(), playlistId);
}

QVector<Section> playlistSections(const QString &dbPath, qint64 playlistId,
                                  QString *error) {
    Connection c(dbPath);
    if (!c.ok) {
        if (error) *error = c.error;
        return {};
    }
    QSqlDatabase db = c.db();
    QSqlQuery q(db);
    q.prepare("SELECT kind FROM playlist WHERE id = ?");
    q.addBindValue(playlistId);
    QString kind = q.exec() && q.next() ? q.value(0).toString() : QString();
    QList<qint64> containers;
    if (kind == "container")
        containers << playlistId;
    else if (kind == "sequence")
        containers = membersOf(db, playlistId);
    QVector<Section> sections;
    for (qint64 cid : containers) {
        QSqlQuery info(db);
        info.prepare("SELECT name, limit_seconds, end_mode, when_empty FROM "
                     "playlist WHERE id = ? AND kind = 'container'");
        info.addBindValue(cid);
        if (!info.exec() || !info.next()) continue;
        Section s;
        s.name = info.value(0).toString();
        s.limitMs =
            info.value(1).isNull() ? -1 : info.value(1).toLongLong() * 1000;
        s.endMode = info.value(2).toString();
        s.whenEmpty = info.value(3).toString();
        for (qint64 pid : membersOf(db, cid)) s.items += entriesOf(db, pid);
        sections.append(s);
    }
    return sections;
}

QList<PlaylistInfo> loadPlaylists(const QString &dbPath, QString *error) {
    Connection c(dbPath);
    if (!c.ok) {
        if (error) *error = c.error;
        return {};
    }
    QSqlDatabase db = c.db();
    QList<PlaylistInfo> list;
    QSqlQuery q(db);
    if (!q.exec("SELECT p.id, p.name, p.kind, p.rules, p.limit_seconds, "
                "p.end_mode, "
                "(SELECT COUNT(*) FROM playlist_item WHERE playlist_id = p.id" +
                QString(hasRemovedMark(db) ? " AND removed_at IS NULL" : "") +
                "), "
                "(SELECT COUNT(*) FROM playlist_member WHERE parent_id = p.id) "
                "FROM playlist p")) {
        if (error)
            *error = "Cannot read the playlists: " + q.lastError().text();
        return list;
    }
    struct Row {
        PlaylistInfo info;
        QVariant limit;
        QString endMode;
        int entries, members;
    };
    QList<Row> rows;
    while (q.next()) {
        Row r;
        r.info.id = q.value(0).toLongLong();
        r.info.name = q.value(1).toString();
        r.info.kind = kindOf(q.value(2), q.value(3));
        r.limit = q.value(4);
        r.endMode = q.value(5).toString();
        r.entries = q.value(6).toInt();
        r.members = q.value(7).toInt();
        rows.append(r);
    }
    std::function<bool(qint64)> holds = [&](qint64 id) {
        for (const Row &o : rows) {
            if (o.info.id != id) continue;
            if (o.info.kind == "dynamic") return true;
            if (o.info.kind == "playlist") return false;
            for (qint64 m : membersOf(db, id))
                if (holds(m)) return true;
        }
        return false;
    };
    for (Row &r : rows) {
        r.info.holdsDynamic = holds(r.info.id);
        QStringList names;
        for (qint64 m : membersOf(db, r.info.id))
            for (const Row &o : rows)
                if (o.info.id == m) names << o.info.name;
        if (r.info.kind == "container") {
            static const QHash<QString, QString> ends = {
                {"fade", "fades out"},
                {"cut", "cut off"},
                {"finish", "finishes the track"}};
            r.info.detail = limitText(r.limit) + ", " +
                            ends.value(r.endMode, r.endMode) + "  ·  " +
                            names.join(", ");
        } else if (r.info.kind == "sequence") {
            r.info.detail =
                QString("%1 containers: ").arg(r.members) + names.join(", ");
        } else {
            r.info.detail = QString("%1 tracks").arg(r.entries) +
                            (r.info.kind == "dynamic" ? "  ·  dynamic" : "");
        }
        list.append(r.info);
    }
    std::sort(list.begin(), list.end(),
              [](const PlaylistInfo &a, const PlaylistInfo &b) {
                  auto rank = [](const QString &k) {
                      return k == "sequence" ? 0 : k == "container" ? 1 : 2;
                  };
                  return rank(a.kind) != rank(b.kind)
                             ? rank(a.kind) < rank(b.kind)
                             : a.name.compare(b.name, Qt::CaseInsensitive) < 0;
              });
    return list;
}

QVariant PlaylistModel::data(const QModelIndex &index, int role) const {
    const PlaylistInfo *p = index.isValid() ? atRow(index.row()) : nullptr;
    if (!p) return {};
    switch (role) {
        case IdRole: return p->id;
        case NameRole: return p->name;
        case KindRole: return p->kind;
        case DetailRole: return p->detail;
        case MatchedRole: return p->matched;
        case GroupRole: return p->kind == "dynamic" ? "playlist" : p->kind;
    }
    return {};
}

QHash<int, QByteArray> PlaylistModel::roleNames() const {
    return {{IdRole, "playlistId"},
            {NameRole, "name"},
            {KindRole, "kind"},
            {DetailRole, "detail"},
            {MatchedRole, "matched"},
            {GroupRole, "group"}};
}

void PlaylistModel::setMatched(const QHash<qint64, int> &counts) {
    for (int row = 0; row < m_items.size(); ++row) {
        auto it = counts.constFind(m_items[row].id);
        if (it == counts.constEnd() || *it == m_items[row].matched) continue;
        m_items[row].matched = *it;
        emit dataChanged(index(row), index(row), {MatchedRole});
    }
}

int PlaylistModel::groups() const {
    QSet<QString> found;
    for (const PlaylistInfo &p : m_items)
        found.insert(p.kind == "dynamic" ? QString("playlist") : p.kind);
    return found.size();
}

void PlaylistModel::setItems(const QList<PlaylistInfo> &items) {
    QHash<qint64, int> known;
    for (const PlaylistInfo &p : m_items)
        if (p.matched >= 0) known.insert(p.id, p.matched);
    beginResetModel();
    m_items = items;
    for (PlaylistInfo &p : m_items) p.matched = known.value(p.id, -1);
    endResetModel();
    emit countChanged();
}

QVariant EntryModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() >= m_items.size()) return {};
    const QueueItem &t = m_items[index.row()];
    switch (role) {
        case IdRole: return t.id;
        case TitleRole: return t.title;
        case ArtistRole: return t.artist;
        case AlbumRole: return t.album;
        case RatingRole: return t.rating;
        case DetailRole: {
            if (m_dynamic || !t.detail.isEmpty()) return t.detail;
            auto it = m_stats.constFind(t.id);
            return it == m_stats.constEnd() ? QString() : songStatText(*it);
        }
    }
    return {};
}

void EntryModel::setStats(const QHash<qint64, SongStat> &stats) {
    m_stats = stats;
    if (!m_items.isEmpty())
        emit dataChanged(index(0), index(m_items.size() - 1), {DetailRole});
}

void EntryModel::setDynamic(bool dynamic) { m_dynamic = dynamic; }

QHash<int, QByteArray> EntryModel::roleNames() const {
    return {{IdRole, "trackId"},
            {TitleRole, "title"},
            {ArtistRole, "artist"},
            {AlbumRole, "album"},
            {RatingRole, "rating"},
            {DetailRole, "detail"}};
}

void EntryModel::setRating(qint64 id, int rating) {
    for (int row = 0; row < m_items.size(); ++row) {
        if (m_items[row].id != id || m_items[row].rating == rating) continue;
        m_items[row].rating = rating;
        emit dataChanged(index(row), index(row), {RatingRole});
    }
}

void EntryModel::setDetails(const QHash<qint64, QString> &why) {
    for (QueueItem &t : m_items) t.detail = why.value(t.id);
    if (!m_items.isEmpty())
        emit dataChanged(index(0), index(m_items.size() - 1), {DetailRole});
}

void EntryModel::setItems(const QVector<QueueItem> &items) {
    bool same = items.size() == m_items.size();
    for (int i = 0; same && i < items.size(); ++i)
        same = items[i].id == m_items[i].id;
    if (same) {  // the same songs: update the rows, so the list stays put
        for (int i = 0; i < items.size(); ++i) {
            const QString detail = m_items[i].detail;
            m_items[i] = items[i];
            m_items[i].detail = detail;
        }
        if (!m_items.isEmpty())
            emit dataChanged(index(0), index(m_items.size() - 1));
        return;
    }
    beginResetModel();
    m_items = items;
    endResetModel();
}
