// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
// Sync with the desktop over the local network (Wi-Fi, or USB tethering),
// driven by the desktop programs' "Sync with Phone"
// (desktop/phone_sync_dialog.py, android/tools/sync_phone.py).  The user
// starts it here.  The desktop finds the phone by a UDP broadcast on port 8766
// ("MUSICPLAYER-SYNC?" is answered with "MUSICPLAYER-SYNC <port>").  Every
// request must carry the pairing code (header X-Pin), which the phone shows and
// keeps until "New code"; five wrong codes lock the server until it is stopped
// and started again.  Small HTTP server:
//   GET  /hello        what this is, the music folder and the free space
//   GET  /db           a consistent copy of tags.db (header X-Generation: how
//   many writes the app has made so far) PUT  /db           the merged
//   database; refused (409) when the app wrote to the database after the copy
//   was taken GET  /files        every file under the music folder with its
//   size, as a JSON object PUT  /file/<path>  one music file (written as
//   <path>.part, then renamed) GET  /ringtones    the files in the phone's
//   Ringtones folder, with their sizes PUT  /ringtone/<name>  one file for the
//   Ringtones folder (a name, no folders; the media scanner is told of it)
//   POST /done         the desktop has finished
// It runs in its own thread, like the playback engine, so it keeps going while
// the phone is locked.
#pragma once
#include <QObject>
#include <QStringList>
#include <QTcpServer>
#include <QUdpSocket>

class SyncServer : public QObject {
    Q_OBJECT
public:
    static constexpr quint16 Port = 8765, DiscoveryPort = 8766;
    explicit SyncServer(QObject *parent = nullptr) : QObject(parent) {}
public slots:
    void configure(const QString &dbPath, const QString &root) {
        m_db = dbPath;
        m_root = root;
    }
    void start();    // listen, answer the desktop's search
    void newCode();  // a new pairing code
    void stop();
signals:
    void running(bool on, const QString &code, const QStringList &addresses);
    void status(const QString &text);
    void databaseReplaced();
    void finished();  // the desktop is done: the server has stopped
private:
    friend class SyncSession;
    QTcpServer *m_server = nullptr;
    QUdpSocket *m_discovery = nullptr;
    QString m_db, m_root, m_code;
    int m_failures = 0, m_files = 0, m_ringtones = 0;
    qint64 m_bytes = 0;
};
