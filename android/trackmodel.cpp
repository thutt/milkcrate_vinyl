// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
#include "trackmodel.h"

#include <QDateTime>
#include <QRegularExpression>
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

// "Plays: 3  ·  Last played: 2 days ago"; empty until the stats are read.
QString TrackModel::statText(QHash<qint64, SongStat>::const_iterator it) const {
    if (it == m_stats.constEnd()) return {};
    QString when = "never";
    const QDateTime then =
        QDateTime::fromString(it->last, "yyyy-MM-ddTHH:mm:ssZ");
    if (then.isValid()) {
        const qint64 days =
            QDateTime(then.date(), then.time(), Qt::UTC)
                .daysTo(QDateTime::currentDateTimeUtc());
        when = days < 1 ? QString("today")
                        : QString("%1 day%2 ago").arg(days).arg(
                              days == 1 ? "" : "s");
    }
    return QString("Plays: %1  \u00b7  Last played: %2")
        .arg(it->plays)
        .arg(when);
}

void TrackModel::setStats(const QHash<qint64, SongStat> &stats) {
    m_stats = stats;
    if (!m_view.isEmpty())
        emit dataChanged(index(0), index(m_view.size() - 1), {DetailRole});
}

QVariant TrackModel::data(const QModelIndex &index, int role) const {
    const Track *t = index.isValid() ? atRow(index.row()) : nullptr;
    if (!t) return {};
    switch (role) {
        case IdRole: return t->id;
        case TitleRole: return t->title;
        case ArtistRole: return t->artist;
        case AlbumRole: return t->album;
        case RatingRole: return t->rating;
        case DetailRole: return statText(m_stats.constFind(t->id));
    }
    return {};
}

QHash<int, QByteArray> TrackModel::roleNames() const {
    return {{IdRole, "trackId"},
            {TitleRole, "title"},
            {ArtistRole, "artist"},
            {AlbumRole, "album"},
            {RatingRole, "rating"},
            {DetailRole, "detail"}};
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
            "SELECT m.id, m.path, m.loudness, t.name, t.value, m.rating FROM "
            "media_file "
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
                    t.rating = q.value(5).isNull() ? -1 : q.value(5).toInt();
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
        t.keyTitle = searchKey(t.title);
        t.keyArtist = searchKey(t.artist);
        t.keyAlbum = searchKey(t.album);
        t.keyGenre = searchKey(t.genre);
        t.keyPath = searchKey(t.path);
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
            {t.id, t.path, t.title, t.artist, t.album, t.loudness, t.measured,
             -1, t.rating});
    }
    return items;
}

void TrackModel::setRating(qint64 id, int rating) {
    auto it = m_index.find(id);
    if (it == m_index.end() || m_all[it.value()].rating == rating) return;
    m_all[it.value()].rating = rating;
    int row = rowOf(id);
    if (row >= 0) emit dataChanged(index(row), index(row), {RatingRole});
}

// "genre:rock" tests one field; the fields are numbered by their position in
// kFields (1 = title).  A word starting with "=" must match a whole word, and
// "quoted words" match as a phrase.  Text before a colon that is not a field
// name is searched for as typed.
static const QStringList kFields = {"", "title", "artist", "album", "genre",
                                    "path"};

QVector<TrackModel::Term> TrackModel::parseQuery(const QString &text) {
    static const QRegularExpression token(
        R"re((?:(\w+):)?(?:"([^"]*)"?|(\S+)))re");
    QVector<Term> terms;
    auto it = token.globalMatch(text.toLower());
    while (it.hasNext()) {
        QRegularExpressionMatch m = it.next();
        QString field = m.captured(1);
        QString word = m.captured(2).isEmpty() ? m.captured(3) : m.captured(2);
        int number = field.isEmpty() ? 0 : kFields.indexOf(field);
        if (number < 1 && !field.isEmpty()) {
            word = field + ':' + word;
            number = 0;
        }
        bool whole = word.startsWith('=');
        QString key = searchKey(whole ? word.mid(1) : word);
        if (!key.isEmpty()) terms.append({number, key, whole});
    }
    return terms;
}

const QString &TrackModel::fieldKey(const Track &t, int field) {
    switch (field) {
    case 1: return t.keyTitle;
    case 2: return t.keyArtist;
    case 3: return t.keyAlbum;
    case 4: return t.keyGenre;
    case 5: return t.keyPath;
    }
    return t.key;
}

void TrackModel::setQuery(const QString &text) {
    QVector<Term> terms = parseQuery(text);
    if (terms == m_terms) return;
    beginResetModel();
    m_terms = terms;
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

// Every search term has to start a word of the track's title, artist, album or
// genre (or of the one field it names).
void TrackModel::rebuild() {
    m_view.clear();
    for (int i = 0; i < m_all.size(); ++i) {
        bool all = true;
        for (const Term &w : m_terms)
            if (!(' ' + fieldKey(m_all[i], w.field) + ' ')
                     .contains(' ' + w.text + (w.whole ? " " : ""))) {
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
