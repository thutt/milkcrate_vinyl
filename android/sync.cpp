// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
#include "sync.h"

#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QJsonDocument>
#ifdef Q_OS_ANDROID
#include <QJniObject>
#endif
#include <QJsonObject>
#include <QMutexLocker>
#include <QNetworkDatagram>
#include <QNetworkInterface>
#include <QRandomGenerator>
#include <QRegularExpression>
#include <QSettings>
#include <QSqlDatabase>
#include <QSqlQuery>
#include <QStorageInfo>
#include <QTcpSocket>
#include <QTimer>
#include <QUrl>
#include <QUuid>
#include <cstdio>

#include "dblock.h"

namespace {

// An own short-lived connection to a database file, closed again when this goes
// out of scope.
struct Connection {
    QString name = "sync-" + QUuid::createUuid().toString(QUuid::Id128);
    bool ok = false;
    explicit Connection(const QString &path) {
        QSqlDatabase db = QSqlDatabase::addDatabase("QSQLITE", name);
        db.setDatabaseName(path);
        db.setConnectOptions("QSQLITE_BUSY_TIMEOUT=3000");
        ok = db.open();
    }
    ~Connection() {
        {
            QSqlDatabase::database(name, false).close();
        }
        QSqlDatabase::removeDatabase(name);
    }
    QSqlDatabase db() const { return QSqlDatabase::database(name, false); }
};

// A usable tags.db: intact and with the library tables.
bool databaseOk(const QString &path) {
    Connection c(path);
    if (!c.ok) return false;
    QSqlQuery q(c.db());
    if (!q.exec("PRAGMA integrity_check") || !q.next() ||
        q.value(0).toString() != "ok")
        return false;
    return q.exec("SELECT value FROM meta WHERE key = 'schema_version'") &&
           q.next() && q.exec("SELECT COUNT(*) FROM media_file");
}

bool replaceFile(const QString &from, const QString &to) {
    if (std::rename(QFile::encodeName(from).constData(),
                    QFile::encodeName(to).constData()) == 0)
        return true;
    QFile::remove(to);  // a card that will not rename over an existing file
    return QFile::rename(from, to);
}

// A relative path from the desktop: no way out of the music folder, no
// characters the card cannot hold.
bool safePath(const QString &path) {
    static const QRegularExpression bad(R"([*?:"<>|\\\x00-\x1f])");
    if (path.isEmpty() || path.startsWith('/') || path.contains(bad))
        return false;
    for (const QString &part : path.split('/'))
        if (part.isEmpty() || part == "." || part == "..") return false;
    return !path.startsWith("tags.db");
}

// What the desktop shows beside the pairing code, so that several phones can be
// told apart.
QString deviceName() {
#ifdef Q_OS_ANDROID
    const QJniObject manufacturer = QJniObject::getStaticObjectField<jstring>(
        "android/os/Build", "MANUFACTURER");
    const QJniObject model =
        QJniObject::getStaticObjectField<jstring>("android/os/Build", "MODEL");
    return (manufacturer.toString() + " " + model.toString()).trimmed();
#else
    return QSysInfo::machineHostName();
#endif
}

}  // namespace

class SyncSession : public QObject {
public:
    SyncSession(QTcpSocket *socket, SyncServer *server)
        : QObject(socket), s(socket), srv(server) {
        connect(s, &QTcpSocket::readyRead, this, &SyncSession::onData);
        connect(s, &QTcpSocket::disconnected, this, &SyncSession::onClosed);
    }
    ~SyncSession() override { discard(); }

private:
    QTcpSocket *s;
    SyncServer *srv;
    QByteArray head;
    bool haveHead = false, finished = false;
    QString method, target;
    QHash<QString, QString> headers;
    QFile out;
    qint64 remaining = 0, total = 0;
    enum Kind { None, Db, File } kind = None;
    QString tempPath, finalPath;

    void onClosed() {
        discard();
        s->deleteLater();
    }
    void discard() {
        if (out.isOpen()) {
            out.close();
            QFile::remove(tempPath);
        }
    }

    void onData() {
        QByteArray data = s->readAll();
        if (!haveHead) {
            head += data;
            int end = head.indexOf("\r\n\r\n");
            if (end < 0) {
                if (head.size() > 16384) reply(431, "headers too large");
                return;
            }
            data = head.mid(end + 4);
            head.truncate(end);
            haveHead = true;
            parse();
            if (finished) return;
        }
        if (out.isOpen() && !data.isEmpty()) {
            qint64 n = qMin<qint64>(data.size(), remaining);
            if (out.write(data.constData(), n) != n) {
                reply(507, "cannot write: " + out.errorString());
                return;
            }
            remaining -= n;
            if (remaining == 0) finishPut();
        }
    }

    void parse() {
        const QList<QByteArray> lines = head.split('\n');
        const QList<QByteArray> first = lines.value(0).trimmed().split(' ');
        if (first.size() < 2) {
            reply(400, "bad request");
            return;
        }
        method = QString::fromLatin1(first[0]);
        target = QString::fromLatin1(first[1]);
        for (int i = 1; i < lines.size(); ++i) {
            int colon = lines[i].indexOf(':');
            if (colon > 0)
                headers.insert(
                    QString::fromLatin1(lines[i].left(colon))
                        .trimmed()
                        .toLower(),
                    QString::fromUtf8(lines[i].mid(colon + 1)).trimmed());
        }
        if (srv->m_failures >= 5) {
            reply(403, "locked: too many wrong codes; stop and start sync on "
                       "the phone");
            return;
        }
        if (headers.value("x-pin") != srv->m_code) {
            ++srv->m_failures;
            reply(403, "wrong code");
            return;
        }
        route();
    }

    void route() {
        if (method == "GET" && target == "/hello") return hello();
        if (method == "GET" && target == "/db") return sendDb();
        if (method == "GET" && target == "/files") return sendFiles();
        if (method == "POST" && target == "/done") {
            emit srv->status(
                QString("Sync finished: %1 music file(s) received (%2 MB).")
                    .arg(srv->m_files)
                    .arg(srv->m_bytes / 1000000));
            reply(200, "ok");
            QTimer::singleShot(300, srv, [srv = srv] {
                srv->stop();
                emit srv->finished();
            });  // after the answer has gone out
            return;
        }
        if (method == "PUT" && target == "/db")
            return startPut(Db, srv->m_db + ".incoming", srv->m_db);
        if (method == "PUT" && target.startsWith("/file/")) {
            QString path = QUrl::fromPercentEncoding(target.mid(6).toUtf8());
            if (!safePath(path)) return reply(400, "bad path");
            return startPut(File, srv->m_root + "/" + path + ".part",
                            srv->m_root + "/" + path);
        }
        reply(404, "unknown request");
    }

    void hello() {
        QJsonObject o;
        o["app"] = "musicplayer-sync";
        o["name"] = deviceName();
        o["version"] = 1;
        o["root"] = srv->m_root;
        QString existing =
            srv->m_root;  // the music folder may not exist yet on a new phone
        while (existing.size() > 1 && !QFileInfo::exists(existing))
            existing.truncate(existing.lastIndexOf('/'));
        o["free"] = double(QStorageInfo(existing).bytesAvailable());
        reply(200, QJsonDocument(o).toJson(QJsonDocument::Compact),
              "application/json");
    }

    void sendDb() {
        if (!QFileInfo::exists(srv->m_db))
            return reply(
                404,
                "no database yet");  // a new phone: the desktop sends its own
        emit srv->status("Sending the database to the desktop…");
        QMutexLocker lock(&dbLock());
        const int generation = dbGeneration();
        const QString snapshot = srv->m_db + ".snapshot";
        QFile::remove(snapshot);
        bool ok = false;
        {
            Connection c(srv->m_db);
            if (c.ok) {
                QSqlQuery q(c.db());
                ok = q.exec("VACUUM INTO '" +
                            QString(snapshot).replace("'", "''") + "'");
            }
        }
        if (!ok) {
            QFile::remove(snapshot);
            ok = QFile::copy(srv->m_db, snapshot);
        }  // the lock keeps the app from writing meanwhile
        QFile f(snapshot);
        if (!ok || !f.open(QIODevice::ReadOnly)) {
            QFile::remove(snapshot);
            return reply(500, "cannot read the database");
        }
        const QByteArray body = f.readAll();
        f.close();
        QFile::remove(snapshot);
        reply(200, body, "application/octet-stream",
              {{"X-Generation", QString::number(generation)}});
    }

    void sendFiles() {
        emit srv->status("Listing the music files…");
        QJsonObject files;
        const QDir root(srv->m_root);
        QDirIterator it(
            srv->m_root, QDir::Files | QDir::Hidden,
            QDirIterator::Subdirectories);  // Hidden: names that start with a
                                            // dot are music too
        while (it.hasNext()) {
            it.next();
            const QString rel = root.relativeFilePath(it.filePath());
            if (rel.startsWith("tags.db") || rel.endsWith(".part")) continue;
            files[rel] = double(it.fileInfo().size());
        }
        reply(200, QJsonDocument(files).toJson(QJsonDocument::Compact),
              "application/json");
    }

    void startPut(Kind k, const QString &temp, const QString &final_) {
        bool numeric = false;
        total = headers.value("content-length").toLongLong(&numeric);
        if (!numeric || total < 0) return reply(411, "Content-Length needed");
        kind = k;
        tempPath = temp;
        finalPath = final_;
        QDir().mkpath(QFileInfo(temp).absolutePath());
        out.setFileName(temp);
        if (!out.open(QIODevice::WriteOnly | QIODevice::Truncate))
            return reply(507,
                         "cannot write " + temp + ": " + out.errorString());
        remaining = total;
        if (kind == Db) emit srv->status("Receiving the merged database…");
        if (remaining == 0) finishPut();
    }

    void finishPut() {
        out.flush();
        out.close();
        if (kind == File) {
            if (!replaceFile(tempPath, finalPath)) {
                QFile::remove(tempPath);
                return reply(500, "cannot rename");
            }
            ++srv->m_files;
            srv->m_bytes += total;
            emit srv->status(QString("Receiving music: %1 file(s), %2 MB")
                                 .arg(srv->m_files)
                                 .arg(srv->m_bytes / 1000000));
            return reply(200, "ok");
        }
        bool replaced = false;
        {
            QMutexLocker lock(&dbLock());
            if (headers.value("x-generation").toInt() != dbGeneration()) {
                QFile::remove(tempPath);
                return reply(409, "the phone wrote to the database after the "
                                  "copy was taken; fetch it again");
            }
            if (!databaseOk(tempPath)) {
                QFile::remove(tempPath);
                return reply(
                    422,
                    "the database failed its check; the phone keeps its own");
            }
            replaced = replaceFile(tempPath, finalPath);
        }
        if (!replaced) {
            QFile::remove(tempPath);
            return reply(500, "cannot replace the database");
        }
        emit srv->status("Database replaced by the merged one.");
        emit srv->databaseReplaced();
        reply(200, "ok");
    }

    void reply(int code, const QString &text) { reply(code, text.toUtf8()); }
    void reply(int code, const char *text) { reply(code, QByteArray(text)); }
    void reply(int code, const QByteArray &body,
               const QByteArray &type = "text/plain",
               const QList<QPair<QString, QString>> &extra = {}) {
        if (finished) return;
        finished = true;
        discard();
        const char *text = code == 200   ? "OK"
                           : code == 409 ? "Conflict"
                                         : "Error";
        QByteArray r = "HTTP/1.1 " + QByteArray::number(code) + " " + text +
                       "\r\nContent-Type: " + type + "\r\nContent-Length: " +
                       QByteArray::number(body.size()) +
                       "\r\nConnection: close\r\n";
        for (const auto &h : extra)
            r += h.first.toLatin1() + ": " + h.second.toLatin1() + "\r\n";
        s->write(r + "\r\n" + body);
        s->disconnectFromHost();
    }
};

// The pairing code is kept between runs, so the desktop only needs it once.
static QString storedCode(bool renew) {
    QSettings settings;
    QString code = settings.value("syncCode").toString();
    if (renew || code.size() != 8) {
        code = QString("%1").arg(QRandomGenerator::system()->bounded(100000000),
                                 8, 10, QChar('0'));
        settings.setValue("syncCode", code);
    }
    return code;
}

static QStringList localAddresses() {
    QStringList addresses;
    for (const QNetworkInterface &i : QNetworkInterface::allInterfaces()) {
        if (!(i.flags() & QNetworkInterface::IsUp) ||
            (i.flags() & QNetworkInterface::IsLoopBack))
            continue;
        for (const QNetworkAddressEntry &e : i.addressEntries())
            if (e.ip().protocol() == QAbstractSocket::IPv4Protocol)
                addresses << e.ip().toString() + "  (" + i.name() + ")";
    }
    return addresses;
}

void SyncServer::start() {
    if (m_server) return;
    m_code = qEnvironmentVariable(
        "MILKCRATEVINYL_SYNC_PIN");  // fixed only for tests on the desktop
    if (m_code.isEmpty()) m_code = storedCode(false);
    m_failures = 0;
    m_files = 0;
    m_bytes = 0;
    m_server = new QTcpServer(this);
    connect(m_server, &QTcpServer::newConnection, this, [this] {
        while (QTcpSocket *s = m_server->nextPendingConnection())
            new SyncSession(s, this);
    });
    if (!m_server->listen(QHostAddress::AnyIPv4, Port)) {
        emit status("Cannot listen on port " + QString::number(Port) + ": " +
                    m_server->errorString());
        delete m_server;
        m_server = nullptr;
        emit running(false, {}, {});
        return;
    }
    // the desktop's search: any datagram "MUSICPLAYER-SYNC?" is answered with
    // the port (not the code)
    m_discovery = new QUdpSocket(this);
    if (m_discovery->bind(QHostAddress::AnyIPv4, DiscoveryPort,
                          QAbstractSocket::ShareAddress |
                              QAbstractSocket::ReuseAddressHint)) {
        connect(m_discovery, &QUdpSocket::readyRead, this, [this] {
            while (m_discovery->hasPendingDatagrams()) {
                QNetworkDatagram d = m_discovery->receiveDatagram();
                if (d.data().trimmed() == "MUSICPLAYER-SYNC?")
                    m_discovery->writeDatagram(QNetworkDatagram(
                        "MUSICPLAYER-SYNC " + QByteArray::number(Port),
                        d.senderAddress(), d.senderPort()));
            }
        });
    }
    emit status("Waiting for the desktop (File > Sync with Phone).");
    emit running(true, m_code, localAddresses());
}

void SyncServer::newCode() {
    if (!m_server) return;
    m_code = storedCode(true);
    m_failures = 0;
    emit running(true, m_code, localAddresses());
}

void SyncServer::stop() {
    if (!m_server) return;
    delete m_discovery;
    m_discovery = nullptr;
    m_server->close();
    delete m_server;  // connections in progress are children of their sockets
                      // and end with them
    m_server = nullptr;
    emit running(false, {}, {});
}
