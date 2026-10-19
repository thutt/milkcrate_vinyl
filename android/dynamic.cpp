// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
#include "dynamic.h"

#include <QDateTime>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMutexLocker>
#include <QRandomGenerator>
#include <QRegularExpression>
#include <QSqlDatabase>
#include <QSqlError>
#include <QSqlQuery>
#include <QUuid>
#include <algorithm>
#include <functional>
#include <limits>
#include <optional>

#include "dblock.h"

namespace {

struct Track {
    qint64 id = 0;
    QString path;
    QHash<QString, QStringList> tags;
    std::optional<qint64> rating;
    qint64 plays = 0;
    QString last;
};

struct Connection {
    QString name = "dynamic-" + QUuid::createUuid().toString(QUuid::Id128);
    bool ok = false;
    QString error;
    explicit Connection(const QString &path) {
        QSqlDatabase db = QSqlDatabase::addDatabase("QSQLITE", name);
        db.setDatabaseName(path);
        db.setConnectOptions("QSQLITE_BUSY_TIMEOUT=3000");
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

const QSet<QString> TextFields = {"artist",   "title", "album", "genre",
                                  "composer", "path",  "format"};
const QSet<QString> NumberFields = {"rating", "plays",           "year",
                                    "bpm",    "bpm_reliability", "track"};

QString fileFormat(const QString &path) {
    const QString name = path.mid(path.lastIndexOf('/') + 1);
    const int dot = name.lastIndexOf('.');
    return dot < 0 ? QString() : name.mid(dot + 1).toLower();
}

std::optional<qint64> firstInt(const QStringList &values) {
    static const QRegularExpression digits(R"(^\s*(\d+))");
    for (const QString &v : values) {
        auto m = digits.match(v);
        if (m.hasMatch()) return m.captured(1).toLongLong();
    }
    return std::nullopt;
}

QStringList textValues(const Track &t, const QString &field) {
    if (field == "path") return {t.path};
    if (field == "format") return {fileFormat(t.path)};
    QStringList out;
    const QStringList keys = field == "artist"
                                 ? QStringList{"ARTIST", "ALBUMARTIST"}
                                 : QStringList{field.toUpper()};
    for (const QString &k : keys)
        for (const QString &v : t.tags.value(k))
            if (!v.trimmed().isEmpty()) out << v;
    return out;
}

std::optional<qint64> numberValue(const Track &t, const QString &field) {
    if (field == "rating") return t.rating;
    if (field == "plays") return t.plays;
    static const QHash<QString, QString> keys = {
        {"year", "DATE"},
        {"bpm", "BPM"},
        {"bpm_reliability", "BPM_RELIABILITY"},
        {"track", "TRACKNUMBER"}};
    return firstInt(t.tags.value(keys.value(field)));
}

using Test = std::function<bool(const Track &)>;

Test compileRule(const QJsonObject &rule, const QDateTime &now) {
    const QString field = rule["field"].toString(), op = rule["op"].toString();
    const QJsonValue value = rule["value"];
    if (TextFields.contains(field)) {
        const QString word = value.toString().trimmed().toLower();
        const QRegularExpression contains(
            "(?<![\\p{L}\\p{N}])" + QRegularExpression::escape(word),
            QRegularExpression::CaseInsensitiveOption |
                QRegularExpression::UseUnicodePropertiesOption);
        const QString whole = word.toCaseFolded();
        auto any = [](const QStringList &vs,
                      std::function<bool(const QString &)> f) {
            return std::any_of(vs.begin(), vs.end(), f);
        };
        return [=](const Track &t) {
            const QStringList vs = textValues(t, field);
            if (op == "contains")
                return any(vs, [&](const QString &v) {
                    return contains.match(v).hasMatch();
                });
            if (op == "not_contains")
                return !any(vs, [&](const QString &v) {
                    return contains.match(v).hasMatch();
                });
            if (op == "is")
                return any(vs, [&](const QString &v) {
                    return v.trimmed().toCaseFolded() == whole;
                });
            if (op == "is_not")
                return !any(vs, [&](const QString &v) {
                    return v.trimmed().toCaseFolded() == whole;
                });
            return vs.isEmpty();  // empty
        };
    }
    if (NumberFields.contains(field)) {
        const qint64 n = qint64(value.toDouble());
        return [=](const Track &t) {
            const auto v = numberValue(t, field);
            if (op == "unset") return !v;
            if (op == "set") return bool(v);
            if (!v) return false;
            if (op == "eq") return *v == n;
            if (op == "ne") return *v != n;
            if (op == "lt") return *v < n;
            if (op == "le") return *v <= n;
            if (op == "gt") return *v > n;
            return *v >= n;  // ge
        };
    }
    const QString since = now.addDays(-qint64(value.toDouble()))
                              .toString("yyyy-MM-ddTHH:mm:ssZ");  // last_played
    return [=](const Track &t) {
        if (op == "within") return !t.last.isEmpty() && t.last >= since;
        if (op == "not_within") return t.last.isEmpty() || t.last < since;
        return t.last.isEmpty();  // never
    };
}

Test compileGroup(const QJsonObject &group, const QDateTime &now) {
    QVector<Test> parts;
    for (const QJsonValue &item : group["rules"].toArray()) {
        const QJsonObject o = item.toObject();
        parts.append(o.contains("field") ? compileRule(o, now)
                                         : compileGroup(o, now));
    }
    const bool all = group["match"].toString() == "all";
    return [parts, all](const Track &t) {
        for (const Test &p : parts)
            if (p(t) != all)
                return !all;  // all: a failing part ends it; any: a passing
                              // part ends it
        return all;
    };
}

void collectFields(const QJsonObject &group, QSet<QString> &fields) {
    for (const QJsonValue &item : group["rules"].toArray()) {
        const QJsonObject o = item.toObject();
        if (o.contains("field"))
            fields.insert(o["field"].toString());
        else
            collectFields(o, fields);
    }
}

// What the sort says about a track: the sort field, then artist / album / track
// number / title (album order for ties).
struct SortKey {
    QString text, artist, album, title;
    qint64 number = 0, track = 0;
    bool operator<(const SortKey &o) const {
        if (text != o.text) return text < o.text;
        if (number != o.number) return number < o.number;
        if (artist != o.artist) return artist < o.artist;
        if (album != o.album) return album < o.album;
        if (track != o.track) return track < o.track;
        return title < o.title;
    }
};

SortKey sortKey(const Track &t, const QString &field) {
    auto text = [&](const QString &f) {
        return textValues(t, f).join(" ").toCaseFolded();
    };
    SortKey k;
    k.artist = text("artist");
    k.album = text("album");
    k.title = text("title");
    k.track = numberValue(t, "track").value_or(0);
    if (field == "artist")
        k.text = k.artist;
    else if (field == "album")
        k.text = k.album;
    else if (field == "title")
        k.text = k.title;
    else if (field == "last_played")
        k.text = t.last;
    else
        k.number = numberValue(t, field).value_or(-1);
    return k;
}

QVector<Track> loadTracks(QSqlDatabase db, const QSet<QString> &ruleFields) {
    QSet<QString> tagNames = {"ARTIST", "ALBUMARTIST", "ALBUM", "TITLE",
                              "TRACKNUMBER"};
    static const QHash<QString, QString> extra = {
        {"genre", "GENRE"},
        {"composer", "COMPOSER"},
        {"year", "DATE"},
        {"bpm", "BPM"},
        {"bpm_reliability", "BPM_RELIABILITY"}};
    for (const QString &f : ruleFields)
        if (extra.contains(f)) tagNames.insert(extra[f]);
    QVector<Track> tracks;
    QHash<qint64, int> index;
    QSqlQuery q(db);
    bool hasDuplicates = false;
    q.exec("PRAGMA table_info(media_file)");
    while (q.next())
        if (q.value(1).toString() == "duplicate_of") hasDuplicates = true;
    q.setForwardOnly(true);
    q.exec(QString("SELECT id, path, rating, play_count, last_played FROM "
                   "media_file") +
           (hasDuplicates ? " WHERE duplicate_of IS NULL" : ""));
    while (q.next()) {
        Track t;
        t.id = q.value(0).toLongLong();
        t.path = q.value(1).toString();
        if (!q.value(2).isNull()) t.rating = q.value(2).toLongLong();
        t.plays = q.value(3).toLongLong();
        t.last = q.value(4).toString();
        index.insert(t.id, tracks.size());
        tracks.append(t);
    }
    QStringList quoted;
    for (const QString &n : tagNames) quoted << "'" + n + "'";
    QSqlQuery tq(db);
    tq.setForwardOnly(true);
    tq.exec("SELECT file_id, name, value FROM tag WHERE name IN (" +
            quoted.join(",") + ") ORDER BY file_id, name, pos");
    while (tq.next()) {
        auto it = index.constFind(tq.value(0).toLongLong());
        if (it != index.constEnd())
            tracks[*it].tags[tq.value(1).toString()] << tq.value(2).toString();
    }
    return tracks;
}

QVector<qint64> chooseFrom(QVector<Track> &tracks, const QJsonObject &rules,
                           const QSet<qint64> &exclude, int limit) {
    const QDateTime now = QDateTime::currentDateTimeUtc();
    const bool filtered = !rules["rules"].toArray().isEmpty();
    const Test selects = filtered ? compileGroup(rules, now)
                                  : Test([](const Track &) { return true; });
    QVector<const Track *> chosen;
    for (const Track &t : tracks)
        if (!exclude.contains(t.id) && selects(t)) chosen.append(&t);
    const QJsonObject sort = rules["sort"].toObject();
    const QString field = sort["field"].toString("random");
    if (field == "random") {
        std::shuffle(chosen.begin(), chosen.end(), *QRandomGenerator::global());
    } else {
        QHash<const Track *, SortKey> keys;
        for (const Track *t : chosen) keys.insert(t, sortKey(*t, field));
        const bool descending = sort["descending"].toBool();
        std::stable_sort(
            chosen.begin(), chosen.end(), [&](const Track *a, const Track *b) {
                return descending ? keys[b] < keys[a] : keys[a] < keys[b];
            });
    }
    QStringList prefer;
    for (const QJsonValue &v : rules["prefer"].toArray())
        prefer << v.toString();
    if (!prefer.isEmpty())  // stable: the order inside each file type stays as
                            // sorted
        std::stable_sort(chosen.begin(), chosen.end(),
                         [&](const Track *a, const Track *b) {
                             auto rank = [&](const Track *t) {
                                 int r = prefer.indexOf(fileFormat(t->path));
                                 return r < 0 ? int(prefer.size()) : r;
                             };
                             return rank(a) < rank(b);
                         });
    QVector<qint64> ids;
    for (const Track *t : chosen) {
        if (ids.size() >= limit) break;
        ids.append(t->id);
    }
    return ids;
}

}  // namespace

QVector<qint64> selectTracks(const QString &dbPath, const QJsonObject &rules,
                             const QSet<qint64> &exclude, int limit,
                             QString *error) {
    Connection c(dbPath);
    if (!c.ok) {
        if (error) *error = c.error;
        return {};
    }
    QSet<QString> fields;
    collectFields(rules, fields);
    QVector<Track> tracks = loadTracks(c.db(), fields);
    return chooseFrom(tracks, rules, exclude, limit);
}

int countMatches(const QString &dbPath, qint64 playlistId) {
    QMutexLocker lock(&dbLock());
    Connection c(dbPath);
    if (!c.ok) return -1;
    QSqlQuery q(c.db());
    q.prepare("SELECT rules FROM playlist WHERE id = ?");
    q.addBindValue(playlistId);
    if (!q.exec() || !q.next() || q.value(0).isNull()) return -1;
    const QJsonObject rules =
        QJsonDocument::fromJson(q.value(0).toString().toUtf8()).object();
    QSet<QString> fields;
    collectFields(rules, fields);
    QVector<Track> tracks = loadTracks(c.db(), fields);
    return chooseFrom(tracks, rules, {}, std::numeric_limits<int>::max())
        .size();
}

QHash<qint64, int> countAllMatches(const QString &dbPath) {
    QMutexLocker lock(&dbLock());
    QHash<qint64, int> counts;
    Connection c(dbPath);
    if (!c.ok) return counts;
    QSqlQuery q(c.db());
    QVector<QPair<qint64, QJsonObject>> lists;
    if (!q.exec("SELECT id, rules FROM playlist WHERE rules IS NOT NULL"))
        return counts;
    QSet<QString> fields;
    while (q.next()) {
        const QJsonObject rules =
            QJsonDocument::fromJson(q.value(1).toString().toUtf8()).object();
        collectFields(rules, fields);
        lists.append({q.value(0).toLongLong(), rules});
    }
    if (lists.isEmpty()) return counts;
    QVector<Track> tracks = loadTracks(c.db(), fields);
    for (const auto &l : lists)
        counts.insert(l.first, chooseFrom(tracks, l.second, {},
                                          std::numeric_limits<int>::max())
                                   .size());
    return counts;
}

int topUpDynamicPlaylists(const QString &dbPath, QString *problem) {
    QMutexLocker lock(&dbLock());
    Connection c(dbPath);
    if (!c.ok) {
        if (problem) *problem = c.error;
        return 0;
    }
    QSqlDatabase db = c.db();
    QSqlQuery q(db);
    q.exec("PRAGMA table_info(playlist_item)");
    bool marks = false;
    while (q.next())
        if (q.value(1).toString() == "removed_at") marks = true;
    int length = 20;
    if (q.exec("SELECT value FROM meta WHERE key = 'dynamic_length'") &&
        q.next())
        length = qMax(1, q.value(0).toInt());
    struct Pending {
        qint64 id;
        QJsonObject rules;
        QSet<qint64> exclude;
        int missing;
        qint64 end;
    };
    QVector<Pending> pending;
    QSqlQuery lists(db);
    lists.exec(
        "SELECT id, rules, played FROM playlist WHERE rules IS NOT NULL");
    while (lists.next()) {
        Pending p;
        p.id = lists.value(0).toLongLong();
        p.rules = QJsonDocument::fromJson(lists.value(1).toString().toUtf8())
                      .object();
        for (const QJsonValue &v :
             QJsonDocument::fromJson(lists.value(2).toString().toUtf8())
                 .array())
            p.exclude.insert(qint64(v.toDouble()));
        QSqlQuery items(db);
        items.prepare("SELECT file_id, pos" +
                      QString(marks ? ", removed_at IS NULL" : ", 1") +
                      " FROM playlist_item WHERE playlist_id = ?");
        items.addBindValue(p.id);
        items.exec();
        int visible = 0;
        p.end = 0;
        while (items.next()) {
            p.exclude.insert(
                items.value(0).toLongLong());  // on the list, played or not:
                                               // not chosen again
            p.end = qMax<qint64>(p.end, items.value(1).toLongLong() + 1);
            if (items.value(2).toInt()) ++visible;
        }
        p.missing = length - visible;
        if (p.missing > 0 && !p.rules.isEmpty()) pending.append(p);
    }
    if (pending.isEmpty()) return 0;
    QSet<QString> fields;
    for (const Pending &p : pending) collectFields(p.rules, fields);
    QVector<Track> tracks = loadTracks(db, fields);
    q.exec("PRAGMA journal_mode = DELETE");
    q.exec("PRAGMA synchronous = FULL");
    int changed = 0;
    if (!db.transaction()) {
        if (problem)
            *problem =
                "Cannot refill the dynamic playlists: " + db.lastError().text();
        return 0;
    }
    for (const Pending &p : pending) {
        const QVector<qint64> ids =
            chooseFrom(tracks, p.rules, p.exclude, p.missing);
        QSqlQuery insert(db);
        insert.prepare("INSERT INTO playlist_item (playlist_id, pos, file_id) "
                       "VALUES (?, ?, ?)");
        qint64 pos = p.end;
        for (qint64 id : ids) {
            insert.addBindValue(p.id);
            insert.addBindValue(pos++);
            insert.addBindValue(id);
            if (!insert.exec()) {
                if (problem)
                    *problem = "Cannot refill the dynamic playlists: " +
                               insert.lastError().text();
                db.rollback();
                return 0;
            }
        }
        if (!ids.isEmpty()) ++changed;
    }
    if (!db.commit()) {
        if (problem)
            *problem =
                "Cannot refill the dynamic playlists: " + db.lastError().text();
        db.rollback();
        return 0;
    }
    if (changed) ++dbGeneration();
    return changed;
}
