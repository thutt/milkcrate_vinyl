// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
#include "dynamic.h"

#include <QDateTime>
#include <QElapsedTimer>
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

// A spelling-insensitive form of a name, as the desktop's normalize().
QString songName(const QString &name) {
    QString out;
    for (QChar c : name.normalized(QString::NormalizationForm_KD))
        if (c.category() != QChar::Mark_NonSpacing) out += c;
    out = out.toCaseFolded();
    static const QRegularExpression joiner("\\s*[&+]\\s*|\\band\\b");
    static const QRegularExpression marks("[^\\w\\s]|_");
    out.replace(joiner, " and ");
    out.remove(marks);
    out = out.simplified();
    if (out.startsWith("the ")) out = out.mid(4);
    return out;
}

// The plays and the latest play of a track are those of its whole song: every
// file with the same artist and title (the copies on other albums and in other
// formats), so that a song played from one copy is not chosen through another.
QHash<qint64, SongStat> computeSongStats(QSqlDatabase db) {
    QHash<qint64, QString> artist, title;
    QSqlQuery n(db);
    n.setForwardOnly(true);
    n.exec("SELECT file_id, name, value FROM tag WHERE name IN ('ARTIST', "
           "'TITLE') ORDER BY file_id, name, pos");
    while (n.next()) {
        const qint64 id = n.value(0).toLongLong();
        const QString value = n.value(2).toString();
        if (value.trimmed().isEmpty()) continue;
        if (n.value(1).toString() == "ARTIST")
            artist[id] += (artist.contains(id) ? " " : "") + value;
        else if (!title.contains(id))
            title[id] = value;
    }
    struct Song {
        qint64 plays = 0;
        QString last;
    };
    QHash<QString, Song> songs;
    QHash<qint64, QString> keyOf;
    QSqlQuery q(db);
    q.setForwardOnly(true);
    q.exec("SELECT id, play_count, last_played FROM media_file");
    while (q.next()) {
        const qint64 id = q.value(0).toLongLong();
        const QString key = artist.contains(id) && title.contains(id)
                                ? songName(artist[id]) + '\x01' +
                                      songName(title[id])
                                : QString("#%1").arg(id);
        keyOf.insert(id, key);
        Song &song = songs[key];
        song.plays += q.value(1).toLongLong();
        const QString last = q.value(2).toString();
        if (last > song.last) song.last = last;
    }
    QHash<qint64, SongStat> out;
    out.reserve(keyOf.size());
    for (auto it = keyOf.constBegin(); it != keyOf.constEnd(); ++it) {
        const Song &song = songs[it.value()];
        out.insert(it.key(), {song.plays, song.last});
    }
    return out;
}

void spreadSongPlays(QSqlDatabase db, QVector<Track> &tracks,
                     const QHash<qint64, SongStat> *known = nullptr) {
    // (the plays of every song are worked out once for several readers)
    const QHash<qint64, SongStat> own =
        known ? QHash<qint64, SongStat>() : computeSongStats(db);
    const QHash<qint64, SongStat> &stats = known ? *known : own;
    for (Track &t : tracks) {
        auto it = stats.constFind(t.id);
        if (it == stats.constEnd()) continue;
        t.plays = it->plays;
        t.last = it->last;
    }
}

// only: just these tracks (when they are few, only their tags are read);
// stats: the plays of every song, when the caller has them already.
QVector<Track> loadTracks(QSqlDatabase db, const QSet<QString> &ruleFields,
                          const QSet<qint64> *only = nullptr,
                          const QHash<qint64, SongStat> *stats = nullptr) {
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
        if (only && !only->contains(t.id)) continue;
        t.path = q.value(1).toString();
        if (!q.value(2).isNull()) t.rating = q.value(2).toLongLong();
        t.plays = q.value(3).toLongLong();
        t.last = q.value(4).toString();
        index.insert(t.id, tracks.size());
        tracks.append(t);
    }
    QStringList quoted;
    for (const QString &n : tagNames) quoted << "'" + n + "'";
    QString only_ids;  // a few tracks: read their tags, not everybody's
    if (only && only->size() <= 500) {
        QStringList ids;
        for (qint64 id : *only) ids << QString::number(id);
        only_ids = " AND file_id IN (" + ids.join(",") + ")";
    }
    QSqlQuery tq(db);
    tq.setForwardOnly(true);
    tq.exec("SELECT file_id, name, value FROM tag WHERE name IN (" +
            quoted.join(",") + ")" + only_ids + " ORDER BY file_id, name, pos");
    while (tq.next()) {
        auto it = index.constFind(tq.value(0).toLongLong());
        if (it != index.constEnd())
            tracks[*it].tags[tq.value(1).toString()] << tq.value(2).toString();
    }
    if (ruleFields.contains("plays") || ruleFields.contains("last_played"))
        spreadSongPlays(db, tracks, stats);
    return tracks;
}

// What makes two files the same song, as the plays are counted
// (computeSongStats and the desktop's song_key): the artist and title,
// spelling aside; empty for a file without both, which is a song of its own.
static QString songKey(const Track &t) {
    QString artist, title;
    for (const QString &v : t.tags.value("ARTIST"))
        if (!v.trimmed().isEmpty()) artist += (artist.isEmpty() ? "" : " ") + v;
    for (const QString &v : t.tags.value("TITLE"))
        if (!v.trimmed().isEmpty()) {
            title = v;
            break;
        }
    if (artist.isEmpty() || title.isEmpty()) return {};
    return songName(artist) + '\x01' + songName(title);
}

// distinct: no two of the chosen are the same song, and none is the song of a
// track in exclude (the copy on a live album is not chosen next to the one on
// the studio album); the first in the order of the choice stands for its song.
QVector<qint64> chooseFrom(QVector<Track> &tracks, const QJsonObject &rules,
                           const QSet<qint64> &exclude, int limit,
                           bool distinct = false) {
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
    QSet<QString> taken;
    if (distinct)
        for (const Track &t : tracks)
            if (exclude.contains(t.id)) taken.insert(songKey(t));
    taken.remove(QString());
    QVector<qint64> ids;
    for (const Track *t : chosen) {
        if (ids.size() >= limit) break;
        if (distinct) {
            const QString key = songKey(*t);
            if (!key.isEmpty()) {
                if (taken.contains(key)) continue;
                taken.insert(key);
            }
        }
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
    fields.insert(rules["sort"].toObject()["field"].toString());
    QVector<Track> tracks = loadTracks(c.db(), fields);
    return chooseFrom(tracks, rules, exclude, limit, true);
}

// How a dynamic playlist's rules see some of the tracks: the values they test,
// one line for each.
static QHash<qint64, QString> explainWith(const QJsonObject &rules,
                                          const QVector<Track> &tracks,
                                          const QSet<qint64> &wanted) {
    const QString sort = rules["sort"].toObject()["field"].toString("random");
    QStringList order;  // the fields in the order the rules name them
    std::function<void(const QJsonObject &)> walk = [&](const QJsonObject &g) {
        for (const QJsonValue &item : g["rules"].toArray()) {
            const QJsonObject o = item.toObject();
            if (o.contains("field")) {
                if (!order.contains(o["field"].toString()))
                    order << o["field"].toString();
            } else {
                walk(o);
            }
        }
    };
    walk(rules);
    if (sort != "random" && !order.contains(sort)) order << sort;
    // The rating and the last play are said for every song, whether or not
    // the rules test them, as the Songs tab does.
    for (const char *f : {"rating", "last_played"})
        if (!order.contains(f)) order << f;
    static const QHash<QString, QString> labels = {
        {"genre", "Genre"},   {"composer", "Composer"}, {"year", "Year"},
        {"bpm", "BPM"},       {"bpm_reliability", "BPM reliability"},
        {"format", "Format"}, {"track", "Track"},       {"rating", "Rating"},
        {"plays", "Plays (any copy)"},
        {"last_played", "Last played (any copy)"}};
    const QDateTime now = QDateTime::currentDateTimeUtc();
    QHash<qint64, QString> out;
    for (const Track &t : tracks) {
        if (!wanted.contains(t.id)) continue;
        QStringList parts;
        for (const QString &f : order) {
            if (!labels.contains(f)) continue;  // title, artist, album, path
            QString text;
            if (f == "rating") {
                const int stars = t.rating ? int(*t.rating) : -1;
                text = stars < 0 ? QString("not rated")
                                 : QString(QChar(0x2605)).repeated(stars) +
                                       QString(QChar(0x2606))
                                           .repeated(5 - stars);
            } else if (f == "plays") {
                text = QString::number(t.plays);
            } else if (f == "last_played") {
                const QDateTime then =
                    QDateTime::fromString(t.last, "yyyy-MM-ddTHH:mm:ssZ");
                if (t.last.isEmpty() || !then.isValid()) {
                    text = "never";
                } else {
                    const qint64 days =
                        QDateTime(then.date(), then.time(), Qt::UTC)
                            .daysTo(now);
                    text = days < 1
                               ? QString("today")
                               : QString("%1 day%2 ago")
                                     .arg(days)
                                     .arg(days == 1 ? "" : "s");
                }
            } else if (f == "format") {
                text = fileFormat(t.path);
            } else if (f == "track") {
                text = numberValue(t, "track")
                           ? QString::number(*numberValue(t, "track"))
                           : QString();
            } else {
                text = textValues(t, f).join("; ");
                if (f == "year" || f == "bpm" || f == "bpm_reliability")
                    text = numberValue(t, f)
                               ? QString::number(*numberValue(t, f))
                               : QString();
            }
            parts << labels[f] + ": " + (text.isEmpty() ? "-" : text);
        }
        out.insert(t.id, parts.join("  \u00b7  "));
    }
    return out;
}


// The counts only read, and take no lock: holding it for the whole rule
// evaluation (over a second on the phone) kept the engine from starting songs.
// stats: the plays of every song, when the caller has them already.
QHash<qint64, QString> explainTracks(const QString &dbPath, qint64 playlistId,
                                     const QList<qint64> &ids,
                                     const QHash<qint64, SongStat> *stats) {
    Connection c(dbPath);
    if (!c.ok) return {};
    QSqlQuery q(c.db());
    q.prepare("SELECT rules FROM playlist WHERE id = ?");
    q.addBindValue(playlistId);
    if (!q.exec() || !q.next() || q.value(0).isNull()) return {};
    const QJsonObject rules =
        QJsonDocument::fromJson(q.value(0).toString().toUtf8()).object();
    QSet<QString> fields;
    collectFields(rules, fields);
    const QString sort = rules["sort"].toObject()["field"].toString("random");
    if (sort != "random") fields.insert(sort);
    fields.insert("last_played");  // the plays of the whole song
    const QSet<qint64> wanted(ids.begin(), ids.end());
    QElapsedTimer clock;
    clock.start();
    QVector<Track> tracks = loadTracks(c.db(), fields, &wanted, stats);
    qInfo("explain %lld songs of %lld: %lld ms", qint64(tracks.size()),
          qint64(ids.size()), qint64(clock.elapsed()));
    return explainWith(rules, tracks, wanted);
}

int countMatches(const QString &dbPath, qint64 playlistId) {
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

// What the lists on the screen need to know about the dynamic playlists after
// a play, from one pass over the library: how many tracks every list's rules
// select, why the open playlist chose its entries, and every song's plays.
DynamicInfo dynamicInfo(const QString &dbPath, qint64 openId,
                        const QList<qint64> &openIds) {
    DynamicInfo info;
    Connection c(dbPath);
    if (!c.ok) return info;
    info.stats = computeSongStats(c.db());
    QSqlQuery q(c.db());
    if (!q.exec("SELECT id, rules FROM playlist WHERE rules IS NOT NULL"))
        return info;
    QVector<QPair<qint64, QJsonObject>> lists;
    QSet<QString> fields;
    while (q.next()) {
        const QJsonObject rules =
            QJsonDocument::fromJson(q.value(1).toString().toUtf8()).object();
        collectFields(rules, fields);
        const QString sort =
            rules["sort"].toObject()["field"].toString("random");
        if (sort != "random") fields.insert(sort);
        fields.insert("last_played");  // for the lines about the open list
        lists.append({q.value(0).toLongLong(), rules});
    }
    if (lists.isEmpty()) return info;
    QVector<Track> tracks = loadTracks(c.db(), fields, nullptr, &info.stats);
    const QSet<qint64> wanted(openIds.begin(), openIds.end());
    for (const auto &l : lists) {
        info.matched.insert(l.first,
                            chooseFrom(tracks, l.second, {},
                                       std::numeric_limits<int>::max())
                                .size());
        if (l.first == openId) info.why = explainWith(l.second, tracks, wanted);
    }
    return info;
}

namespace {

// A dynamic playlist that is short of entries, and the tracks chosen for it.
struct Pending {
    qint64 id;
    QJsonObject rules;
    QSet<qint64> exclude;
    int missing;
    qint64 end;
    QVector<qint64> chosen;
};

// The dynamic playlists that have fewer visible entries than their length.
QVector<Pending> shortLists(QSqlDatabase db) {
    QSqlQuery q(db);
    q.exec("PRAGMA table_info(playlist_item)");
    bool marks = false;
    while (q.next())
        if (q.value(1).toString() == "removed_at") marks = true;
    int length = 20;
    if (q.exec("SELECT value FROM meta WHERE key = 'dynamic_length'") &&
        q.next())
        length = qMax(1, q.value(0).toInt());
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
    return pending;
}

}  // namespace

// Reading the lists and choosing among the whole library (over a second on the
// phone) happen without the lock, which would keep the other writers (a
// rating, a play) waiting; only writing the chosen tracks takes it.  If
// something wrote to the database meanwhile (dbGeneration changed) the choice
// is made again from what is there now.
int topUpDynamicPlaylists(const QString &dbPath, QString *problem) {
    for (int attempt = 0; attempt < 3; ++attempt) {
        const int generation = dbGeneration();
        QVector<Pending> pending;
        {
            Connection c(dbPath);
            if (!c.ok) {
                if (problem) *problem = c.error;
                return 0;
            }
            pending = shortLists(c.db());
            if (pending.isEmpty()) return 0;
            QSet<QString> fields;
            for (const Pending &p : pending) collectFields(p.rules, fields);
            QVector<Track> tracks = loadTracks(c.db(), fields);
            for (Pending &p : pending)
                p.chosen = chooseFrom(tracks, p.rules, p.exclude, p.missing,
                                      true);
        }
        QMutexLocker lock(&dbLock());
        if (generation != dbGeneration()) continue;  // choose again
        Connection c(dbPath);
        if (!c.ok) {
            if (problem) *problem = c.error;
            return 0;
        }
        QSqlDatabase db = c.db();
        QSqlQuery q(db);
        q.exec("PRAGMA journal_mode = DELETE");
        q.exec("PRAGMA synchronous = FULL");
        int changed = 0;
        if (!db.transaction()) {
            if (problem)
                *problem = "Cannot refill the dynamic playlists: " +
                           db.lastError().text();
            return 0;
        }
        for (const Pending &p : pending) {
            QSqlQuery insert(db);
            insert.prepare("INSERT INTO playlist_item (playlist_id, pos, "
                           "file_id) VALUES (?, ?, ?)");
            qint64 pos = p.end;
            for (qint64 id : p.chosen) {
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
            if (!p.chosen.isEmpty()) ++changed;
        }
        if (!db.commit()) {
            if (problem)
                *problem = "Cannot refill the dynamic playlists: " +
                           db.lastError().text();
            db.rollback();
            return 0;
        }
        if (changed) ++dbGeneration();
        return changed;
    }
    return 0;  // the database kept changing: the next play tries again
}

QHash<qint64, SongStat> songStats(const QString &dbPath) {
    Connection c(dbPath);
    if (!c.ok) return {};
    return computeSongStats(c.db());
}

double trackBpm(const QString &dbPath, qint64 id) {
    Connection c(dbPath);
    if (!c.ok) return 0;
    QSqlQuery q(c.db());
    q.prepare("SELECT value FROM tag WHERE file_id = ? AND name = 'BPM' "
              "ORDER BY pos LIMIT 1");
    q.addBindValue(id);
    if (!q.exec() || !q.next()) return 0;
    bool ok = false;
    const double bpm = q.value(0).toString().trimmed().toDouble(&ok);
    return ok && bpm > 0 ? bpm : 0;
}
