// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
#include "player.h"

#include "buildstamp.h"

#include "dynamic.h"
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include "eqpresets.h"

#include <QDebug>
#include <QDir>
#include <QDateTime>
#include <QFile>
#include <QFileInfo>
#include <QMutex>
#include <QGuiApplication>
#include <QRandomGenerator>
#include <QStandardPaths>
#include <QThreadPool>
#include <algorithm>
#include <chrono>

#ifdef Q_OS_ANDROID
#include <QCoreApplication>
#include <QJniEnvironment>
#include <QJniObject>
#endif

#ifdef Q_OS_ANDROID
static Engine *g_engine = nullptr;

// PlaybackService.command(what, argument): runs on the Java thread; the
// engine's own thread carries it out (the main thread may be stopped while the
// phone is locked).
static void nativeCommand(JNIEnv *, jclass, jint what, jlong argument) {
    if (g_engine)
        QMetaObject::invokeMethod(
            g_engine,
            [what, argument] { g_engine->handleCommand(what, argument); },
            Qt::QueuedConnection);
}

static void nativeLog(JNIEnv *env, jclass, jstring line) {
    const char *chars = env->GetStringUTFChars(line, nullptr);
    qInfo("%s", chars);
    env->ReleaseStringUTFChars(line, chars);
}
#endif

Player::Player(QObject *parent)
    : QObject(parent), m_engine(new Engine), m_sync(new SyncServer),
      m_settings() {
    m_volume = m_settings.value("volume", 1.0).toDouble();
    m_target = m_settings.value("target", -10.0).toDouble();
    m_leveling = m_settings.value("leveling", true).toBool();
    m_eqOn = m_settings.value("eqOn", false).toBool();
    m_eqAuto = m_settings.value("eqAuto", false).toBool();
    QStringList saved = m_settings.value("eqGains").toStringList();
    for (int i = 0; i < saved.size() && i < m_eqGains.size(); ++i)
        m_eqGains[i] = std::clamp(saved[i].toDouble(), -12.0, 12.0);
    const QJsonArray custom = QJsonDocument::fromJson(
        m_settings.value("eqCustom").toString().toUtf8()).array();
    for (const QJsonValue &v : custom) {
        CustomEq c;
        c.name = v.toObject()["name"].toString();
        for (const QJsonValue &g : v.toObject()["gains"].toArray())
            c.gains << std::clamp(g.toDouble(), -12.0, 12.0);
        c.gains.resize(5);
        if (!c.name.isEmpty()) m_eqCustom << c;
    }
    m_shuffle = m_settings.value("shuffle", false).toBool();

    m_engine->moveToThread(&m_thread);
    connect(&m_thread, &QThread::finished, m_engine, &QObject::deleteLater);
    connect(m_engine, &Engine::current, this,
            [this](qint64 id, const QString &title, const QString &artist,
                   const QString &album) {
                m_currentId = id;
                m_title = title;
                m_artist = artist;
                m_album = album;
                m_rating = -1;
                emit ratingChanged();
                emit currentChanged();
                showBpm(id);
            });
    connect(m_engine, &Engine::ratingKnown, this,
            [this](qint64 id, int rating) {
                m_model.setRating(id, rating);
                m_entries.setRating(id, rating);
                if (id == m_currentId && rating != m_rating) {
                    m_rating = rating;
                    emit ratingChanged();
                }
            });
    connect(m_engine, &Engine::playingChanged, this,
            [this] { emit playingChanged(); });
    connect(m_engine, &Engine::message, this,
            [this](const QString &text) { say(text); });
    connect(m_engine, &Engine::playCounted, this,
            [this] { reloadPlaylists(); });
    m_thread.start();
    m_sync->moveToThread(&m_syncThread);
    connect(m_engine, &Engine::eqChosen, this, [this](const QString &name) {
        if (name == m_eqAutoName) return;
        m_eqAutoName = name;
        emit eqChanged();
    });
    connect(&m_syncThread, &QThread::finished, m_sync, &QObject::deleteLater);
    connect(m_sync, &SyncServer::running, this,
            [this](bool on, const QString &code, const QStringList &addresses) {
                m_syncRunning = on;
                m_syncCode = code;
                m_syncAddresses = addresses.join("\n");
                emit syncChanged();
                keepScreenOn(on);
            });
    connect(m_sync, &SyncServer::status, this, [this](const QString &text) {
        m_syncStatus = text;
        emit syncChanged();
    });
    connect(m_sync, &SyncServer::finished, this,
            [this] { emit syncFinished(); });
    connect(m_sync, &SyncServer::databaseReplaced, this, [this] { reload(); });
    m_syncThread.start();
    onEngine([e = m_engine] { e->init(); });
    pushVolume();
    pushEq();
    onEngine([e = m_engine, s = m_shuffle] { e->setShuffle(s); });
#ifdef Q_OS_ANDROID
    g_engine = m_engine;
    QJniEnvironment env;
    env.registerNativeMethods(
        "org/musicplayer/player/PlaybackService",
        {{"command", "(IJ)V", reinterpret_cast<void *>(nativeCommand)},
         {"log", "(Ljava/lang/String;)V",
          reinterpret_cast<void *>(nativeLog)}});
#endif
    connect(&m_poll, &QTimer::timeout, this, [this] {
        qint64 p = m_engine->position, d = m_engine->duration;
        if (p != m_lastPosition) {
            m_lastPosition = p;
            emit positionChanged();
        }
        if (d != m_lastDuration) {
            m_lastDuration = d;
            emit durationChanged();
        }
        QString section = m_engine->sectionText();
        if (section != m_sectionText) {
            m_sectionText = section;
            emit sectionChanged();
        }
    });
    m_poll.start(250);
}

QString Player::build() const { return QStringLiteral(MILKCRATE_BUILD); }

Player::~Player() {
    QThreadPool::globalInstance()->waitForDone(
        3000);  // a refill of dynamic playlists may be running
#ifdef Q_OS_ANDROID
    g_engine = nullptr;
#endif
    m_thread.quit();
    m_thread.wait();
    QMetaObject::invokeMethod(m_sync, &SyncServer::stop,
                              Qt::BlockingQueuedConnection);
    m_syncThread.quit();
    m_syncThread.wait();
}

// The screen stays on while syncing: the sync runs in this app and a locked
// phone would stop it.
void Player::keepScreenOn(bool on) {
#ifdef Q_OS_ANDROID
    QNativeInterface::QAndroidApplication::runOnAndroidMainThread([on] {
        QJniObject activity = QNativeInterface::QAndroidApplication::context();
        QJniObject window =
            activity.callObjectMethod("getWindow", "()Landroid/view/Window;");
        if (window.isValid())
            window.callMethod<void>(on ? "addFlags" : "clearFlags", "(I)V",
                                    jint(128));  // FLAG_KEEP_SCREEN_ON
    });
#else
    Q_UNUSED(on);
#endif
}

void Player::rate(int stars) {
    if (m_currentId < 0) return;
    const int value = stars == m_rating && stars > 0 ? 0 : stars;
    onEngine(
        [e = m_engine, id = m_currentId, value] { e->setRating(id, value); });
}

void Player::clearRating() {
    if (m_currentId < 0) return;
    onEngine([e = m_engine, id = m_currentId] { e->setRating(id, -1); });
}

void Player::startSync() {
    QMetaObject::invokeMethod(
        m_sync,
        [s = m_sync, d = m_dbPath, r = m_root] {
            s->configure(d, r);
            s->start();
        },
        Qt::QueuedConnection);
}

void Player::newSyncCode() {
    QMetaObject::invokeMethod(m_sync, &SyncServer::newCode,
                              Qt::QueuedConnection);
}

void Player::stopSync() {
    QMetaObject::invokeMethod(m_sync, &SyncServer::stop, Qt::QueuedConnection);
}

void Player::say(const QString &text) {
    m_message = text;
    emit messageChanged();
}

// Where the app keeps the database and, by default, looks for the music: its
// own folder on the SD card when there is one (it needs no permission), else on
// the phone.
QString Player::defaultDataDir() const {
    QString dir = qEnvironmentVariable("MILKCRATEVINYL_DIR");
#ifdef Q_OS_ANDROID
    if (dir.isEmpty()) {
        QJniObject context = QNativeInterface::QAndroidApplication::context();
        QJniObject dirs = context.callObjectMethod(
            "getExternalFilesDirs", "(Ljava/lang/String;)[Ljava/io/File;",
            nullptr);
        QJniEnvironment env;
        jobjectArray array = dirs.object<jobjectArray>();
        for (jsize i = 0; i < env->GetArrayLength(array); ++i) {
            QJniObject file(env->GetObjectArrayElement(array, i));
            if (file.isValid()) {
                QString path = file.callObjectMethod<jstring>("getAbsolutePath")
                                   .toString();
                if (dir.isEmpty() || i > 0)
                    dir = path;  // the last entry is the SD card, when there is
                                 // one
            }
        }
    }
#endif
    if (dir.isEmpty())
        dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    return dir;
}

// The library folder: <SD card>/MusicLibrary, else <phone
// storage>/MusicLibrary, holding tags.db and the music (cleaned names, see
// tools/copy_library.py).  Without an SD card and without such a folder the
// app's own folder is used.
QString Player::findLibrary() const {
    QStringList candidates;
    QString sd;
#ifdef Q_OS_ANDROID
    QJniObject context = QNativeInterface::QAndroidApplication::context();
    QJniObject dirs = context.callObjectMethod(
        "getExternalFilesDirs", "(Ljava/lang/String;)[Ljava/io/File;", nullptr);
    QJniEnvironment env;
    jobjectArray array = dirs.object<jobjectArray>();
    for (jsize i = 1; i < env->GetArrayLength(array);
         ++i) {  // 0 is the phone's own storage, the rest are cards
        QJniObject file(env->GetObjectArrayElement(array, i));
        if (!file.isValid()) continue;
        QString path =
            file.callObjectMethod<jstring>("getAbsolutePath").toString();
        int cut = path.indexOf("/Android/data/");
        if (cut > 0) {
            sd = path.left(cut) + "/MusicLibrary";
            candidates << sd;
        }
    }
    candidates << "/storage/emulated/0/MusicLibrary";
#endif
    if (qEnvironmentVariableIsSet("MILKCRATEVINYL_LIBRARY"))
        candidates.prepend(qEnvironmentVariable("MILKCRATEVINYL_LIBRARY"));
    for (const QString &c : candidates)
        if (QFileInfo::exists(c + "/tags.db")) return c;
    return sd;  // a card is there but the library (or the permission) is not
}

void Player::checkFilesAccess() {
    bool needs = false;
#ifdef Q_OS_ANDROID
    if (QNativeInterface::QAndroidApplication::sdkVersion() >= 30 &&
        !m_dbPath.contains("/Android/data/"))
        needs = !QJniObject::callStaticMethod<jboolean>(
            "android/os/Environment", "isExternalStorageManager", "()Z");
#endif
    if (needs != m_needsFilesAccess) {
        m_needsFilesAccess = needs;
        emit filesAccessChanged();
        if (!needs && !m_model.total())
            reload();  // the user has just allowed it
    }
}

void Player::requestFilesAccess() {
#ifdef Q_OS_ANDROID
    QJniObject activity = QNativeInterface::QAndroidApplication::context();
    QJniObject action = QJniObject::getStaticObjectField<jstring>(
        "android/provider/Settings",
        "ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION");
    QJniObject intent("android/content/Intent", "(Ljava/lang/String;)V",
                      action.object<jstring>());
    QJniObject uri = QJniObject::callStaticObjectMethod(
        "android/net/Uri", "parse", "(Ljava/lang/String;)Landroid/net/Uri;",
        QJniObject::fromString("package:org.musicplayer.player")
            .object<jstring>());
    intent.callObjectMethod(
        "setData", "(Landroid/net/Uri;)Landroid/content/Intent;", uri.object());
    activity.callMethod<void>("startActivity", "(Landroid/content/Intent;)V",
                              intent.object());
#endif
}

void Player::requestAudioPermission() {
#ifdef Q_OS_ANDROID
    QJniObject activity = QNativeInterface::QAndroidApplication::context();
    QStringList missing;
    for (const char *permission : {"android.permission.READ_MEDIA_AUDIO",
                                   "android.permission.POST_NOTIFICATIONS"}) {
        QJniObject name = QJniObject::fromString(permission);
        if (activity.callMethod<jint>("checkSelfPermission",
                                      "(Ljava/lang/String;)I",
                                      name.object<jstring>()) != 0)
            missing << permission;
    }
    if (missing.isEmpty()) return;
    QJniEnvironment env;
    jobjectArray array = env->NewObjectArray(
        missing.size(), env->FindClass("java/lang/String"), nullptr);
    for (int i = 0; i < missing.size(); ++i)
        env->SetObjectArrayElement(
            array, i, QJniObject::fromString(missing[i]).object<jstring>());
    activity.callMethod<void>("requestPermissions", "([Ljava/lang/String;I)V",
                              array, 1);
#endif
}

// Everything the app logs is also kept in a file next to tags.db, because the
// system log is small and is lost when the phone is switched off.  The file is
// started over when it passes 4 MB (the old one is kept as .1).
static QFile *g_logFile = nullptr;
static QtMessageHandler g_oldHandler = nullptr;

static void logToFile(QtMsgType type, const QMessageLogContext &context,
                      const QString &text) {
    static QMutex mutex;
    if (g_oldHandler) g_oldHandler(type, context, text);
    QMutexLocker lock(&mutex);
    if (!g_logFile || !g_logFile->isOpen()) return;
    const char *level = type == QtWarningMsg    ? "W"
                        : type == QtCriticalMsg ? "E"
                        : type == QtFatalMsg    ? "F"
                                                : "I";
    g_logFile->write(QString("%1 %2 %3\n")
                         .arg(QDateTime::currentDateTime().toString(
                             "MM-dd HH:mm:ss.zzz"))
                         .arg(level)
                         .arg(text)
                         .toUtf8());
    g_logFile->flush();
    if (g_logFile->size() > 4000000) {
        const QString path = g_logFile->fileName();
        g_logFile->close();
        QFile::remove(path + ".1");
        QFile::rename(path, path + ".1");
        g_logFile->open(QIODevice::WriteOnly | QIODevice::Append);
    }
}

static void startLogFile(const QString &path) {
    if (g_logFile) return;
    g_logFile = new QFile(path);
    if (!g_logFile->open(QIODevice::WriteOnly | QIODevice::Append)) {
        delete g_logFile;
        g_logFile = nullptr;
        return;
    }
    g_oldHandler = qInstallMessageHandler(logToFile);
    qInfo("---- app started, build %s", MILKCRATE_BUILD);
}

void Player::start() {
    if (qEnvironmentVariableIsSet("MILKCRATEVINYL_MUTE"))
        onEngine([e = m_engine] { e->setMuted(true); });  // for tests
    requestAudioPermission();
    QString library = findLibrary();
    QString dir = defaultDataDir();
    m_dbPath = m_settings
                   .value("libraryDb", library.isEmpty() ? dir + "/tags.db"
                                                         : library + "/tags.db")
                   .toString();
    m_root =
        m_settings
            .value("libraryRoot", library.isEmpty() ? dir + "/music" : library)
            .toString();
    onEngine([e = m_engine, r = m_root, d = m_dbPath] {
        e->setRoot(r);
        e->setDatabase(d);
    });
    startLogFile(QFileInfo(m_dbPath).absolutePath() + "/milkcrate-log.txt");
    emit pathsChanged();
    checkFilesAccess();
    reload();
    if (qEnvironmentVariableIsSet("MILKCRATEVINYL_SYNC"))
        startSync();  // for tests
    connect(qGuiApp, &QGuiApplication::applicationStateChanged, this,
            [this](Qt::ApplicationState s) {
                if (s == Qt::ApplicationActive)
                    checkFilesAccess();  // back from the system screen
            });
}

void Player::setPaths(const QString &db, const QString &root) {
    m_dbPath = db.trimmed();
    m_root = root.trimmed();
    while (m_root.size() > 1 && m_root.endsWith('/')) m_root.chop(1);
    m_settings.setValue("libraryDb", m_dbPath);
    m_settings.setValue("libraryRoot", m_root);
    onEngine([e = m_engine, r = m_root, d = m_dbPath] {
        e->setRoot(r);
        e->setDatabase(d);
    });
    emit pathsChanged();
    checkFilesAccess();
    reload();
}

void Player::reload() {
    if (!QFileInfo::exists(m_dbPath)) {
        say("No database at " + m_dbPath);
        return;
    }
    QString error;
    if (!m_model.load(m_dbPath, &error)) {
        say(error);
        return;
    }
    QString playlistError;
    m_playlists.setItems(loadPlaylists(m_dbPath, &playlistError));
    countMatched();
    refreshStats();
    if (!playlistError.isEmpty()) {
        say(playlistError);
        return;
    }
    say(QFileInfo(m_root).isDir() ? QString()
                                  : "Music folder not found: " + m_root);
}

void Player::setShuffle(bool on) {
    if (on == m_shuffle || !shuffleAvailable()) return;
    m_shuffle = on;
    m_settings.setValue("shuffle", on);
    onEngine([e = m_engine, on] { e->setShuffle(on); });
    emit shuffleChanged();
}

double Player::level(int band) const {
    return band >= 0 && band < BandMeter::Bands ? double(m_engine->level[band])
                                                : 0.0;
}

bool Player::levelsLive() const {
    qint64 at = m_engine->levelAt;
    if (at == 0) return false;
    qint64 now = std::chrono::duration_cast<std::chrono::milliseconds>(
                     std::chrono::steady_clock::now().time_since_epoch())
                     .count();
    return now - at < 400;
}

void Player::pushVolume() {
    onEngine([e = m_engine, v = m_volume, t = m_target, l = m_leveling] {
        e->setVolume(v, t, l);
    });
}
void Player::setVolume(double v) {
    m_volume = v;
    m_settings.setValue("volume", v);
    pushVolume();
    emit volumeChanged();
}
void Player::setTarget(double v) {
    m_target = v;
    m_settings.setValue("target", v);
    pushVolume();
    emit volumeChanged();
}
void Player::setLeveling(bool on) {
    m_leveling = on;
    m_settings.setValue("leveling", on);
    pushVolume();
    emit volumeChanged();
}

// -- equalizer --


QStringList Player::eqBands() const {
    return {"60 Hz", "230 Hz", "910 Hz", "3.6 kHz", "14 kHz"};
}

QStringList Player::eqPresets() const {
    QStringList names;
    for (int i = 0; i < eqPresetCount; ++i) names << ::eqPresets[i].name;
    return names + eqCustomNames();
}

QStringList Player::eqCustomNames() const {
    QStringList names;
    for (const CustomEq &c : m_eqCustom) names << c.name;
    return names;
}

// The gains of a built-in or saved preset.
bool Player::presetGains(const QString &name, QVector<double> &gains) const {
    for (int i = 0; i < eqPresetCount; ++i)
        if (name == ::eqPresets[i].name) {
            gains = QVector<double>(::eqPresets[i].gains,
                                    ::eqPresets[i].gains + 5);
            return true;
        }
    for (const CustomEq &c : m_eqCustom)
        if (name == c.name) {
            gains = c.gains;
            return true;
        }
    return false;
}

void Player::saveEqCustom() {
    QJsonArray list;
    for (const CustomEq &c : m_eqCustom) {
        QJsonArray gains;
        for (double g : c.gains) gains << g;
        list << QJsonObject{{"name", c.name}, {"gains", gains}};
    }
    m_settings.setValue(
        "eqCustom",
        QString::fromUtf8(QJsonDocument(list).toJson(QJsonDocument::Compact)));
}

QVariantList Player::eqGains() const {
    QVariantList list;
    for (double g : m_eqGains) list << g;
    return list;
}

QString Player::eqPreset() const {
    for (int i = 0; i < eqPresetCount; ++i)
        if (std::equal(m_eqGains.begin(), m_eqGains.end(),
                       ::eqPresets[i].gains))
            return ::eqPresets[i].name;
    for (const CustomEq &c : m_eqCustom)
        if (c.gains == m_eqGains) return c.name;
    return "Custom";
}

QVariantList Player::eqShownGains() const {
    QVector<double> gains;
    if (m_eqAuto && presetGains(m_eqAutoName, gains)) {
        QVariantList list;
        for (double g : gains) list << g;
        return list;
    }
    return eqGains();
}

QString Player::eqShownPreset() const {
    return m_eqAuto && !m_eqAutoName.isEmpty() ? m_eqAutoName : eqPreset();
}

QString Player::eqSummary() const {
    if (!m_eqOn) return "Equalizer off";
    return "Equalizer: " + eqShownPreset() +
           (m_eqAuto && !m_eqAutoName.isEmpty() ? " (auto)" : "");
}

void Player::pushEq() {
    onEngine([e = m_engine, on = m_eqOn, g = m_eqGains, a = m_eqAuto,
              c = m_eqCustom] {
        e->setEqualizer(on, g, a, c);
    });
}

void Player::setEqOn(bool on) {
    m_eqOn = on;
    m_settings.setValue("eqOn", on);
    pushEq();
    emit eqChanged();
}

void Player::setEqAuto(bool on) {
    m_eqAuto = on;
    m_settings.setValue("eqAuto", on);
    pushEq();
    emit eqChanged();
}

void Player::setEqBand(int band, double gainDb) {
    if (band < 0 || band >= m_eqGains.size()) return;
    m_eqGains[band] = std::clamp(gainDb, -12.0, 12.0);
    QStringList saved;
    for (double g : m_eqGains) saved << QString::number(g);
    m_settings.setValue("eqGains", saved);
    pushEq();
    emit eqChanged();
}

void Player::setEqPreset(const QString &name) {
    QVector<double> gains;
    if (!presetGains(name, gains)) return;
    m_eqGains = gains;
    QStringList saved;
    for (double g : m_eqGains) saved << QString::number(g);
    m_settings.setValue("eqGains", saved);
    pushEq();
    emit eqChanged();
}

QString Player::saveEqPreset(const QString &text) {
    const QString name = text.simplified();
    if (name.isEmpty()) return "Give the preset a name.";
    for (int i = 0; i < eqPresetCount; ++i)
        if (name.compare(::eqPresets[i].name, Qt::CaseInsensitive) == 0 ||
            name.compare("Custom", Qt::CaseInsensitive) == 0)
            return "That name is used by a built-in preset.";
    int at = 0;
    while (at < m_eqCustom.size() &&
           m_eqCustom[at].name.compare(name, Qt::CaseInsensitive) != 0)
        ++at;
    if (at == m_eqCustom.size()) m_eqCustom << CustomEq();
    m_eqCustom[at] = {name, m_eqGains};
    std::sort(m_eqCustom.begin(), m_eqCustom.end(),
              [](const CustomEq &a, const CustomEq &b) {
                  return a.name.compare(b.name, Qt::CaseInsensitive) < 0;
              });
    saveEqCustom();
    pushEq();
    emit eqChanged();
    return "";
}

void Player::deleteEqPreset(const QString &name) {
    for (int i = 0; i < m_eqCustom.size(); ++i)
        if (m_eqCustom[i].name == name) {
            m_eqCustom.removeAt(i);
            saveEqCustom();
            pushEq();
            emit eqChanged();
            return;
        }
}

void Player::setPlayingList(qint64 id) {
    if (id == m_playingList) return;
    m_playingList = id;
    emit playingListChanged();
    emit shuffleChanged();  // a dynamic playlist plays without shuffle
}

bool Player::isDynamic(qint64 playlistId) const {
    for (int row = 0; row < m_playlists.rowCount(); ++row) {
        const PlaylistInfo *p = m_playlists.atRow(row);
        if (p && p->id == playlistId) return p->holdsDynamic;
    }
    return false;
}

void Player::playRow(int row) {
    QVector<QueueItem> items = m_model.viewItems();
    if (row < 0 || row >= items.size()) return;
    if (items[row].id == m_currentId) {
        resumeCurrent();
        return;
    }
    setPlayingList(-1);
    onEngine([e = m_engine, items = std::move(items), row,
              shuffle = m_shuffle] { e->playQueue(items, row, shuffle); });
}

// Poking the song that is already current never restarts it, and never changes
// the queue or the list it came from.
void Player::resumeCurrent() {
    onEngine([e = m_engine] { e->resume(); });
}

void Player::moveToBack() {
#ifdef Q_OS_ANDROID
    QJniObject activity = QNativeInterface::QAndroidApplication::context();
    if (activity.isValid())
        activity.callMethod<jboolean>("moveTaskToBack", "(Z)Z", jboolean(true));
#endif
}

void Player::toggle() {
    if (m_currentId < 0) {
        playRow(0);
        return;
    }
    onEngine([e = m_engine] { e->toggle(); });
}

void Player::next() {
    onEngine([e = m_engine] { e->next(); });
}
void Player::previous() {
    onEngine([e = m_engine] { e->previous(); });
}
void Player::seek(qint64 ms) {
    onEngine([e = m_engine, ms] { e->seek(ms); });
}

void Player::playPlaylist(int row) {
    const PlaylistInfo *p = m_playlists.atRow(row);
    if (!p) return;
    if (p->kind == "container" || p->kind == "sequence") {
        QVector<Section> sections = playlistSections(m_dbPath, p->id);
        setPlayingList(p->id);
        onEngine([e = m_engine, sections = std::move(sections),
                  shuffle = shuffle()] { e->playSections(sections, shuffle); });
        return;
    }
    QVector<QueueItem> items = playlistItems(m_dbPath, p->id);
    if (items.isEmpty()) {
        say("“" + p->name + "” has no tracks.");
        return;
    }
    const bool shuffle = m_shuffle && !p->holdsDynamic;
    int start =
        shuffle
            ? int(QRandomGenerator::global()->bounded(quint32(items.size())))
            : 0;
    setPlayingList(p->id);
    onEngine([e = m_engine, items = std::move(items), start, shuffle,
              id = p->id] {
        e->playQueue(items, start, shuffle, id);
    });
}

// How many library tracks each dynamic playlist's rules select, worked out in
// the background (the library is read once).
void Player::countMatched() {
    QThreadPool::globalInstance()->start([this, path = m_dbPath] {
        const QHash<qint64, int> counts = countAllMatches(path);
        QMetaObject::invokeMethod(
            this, [this, counts] { m_playlists.setMatched(counts); },
            Qt::QueuedConnection);
    });
}

void Player::openPlaylist(int row) {
    const PlaylistInfo *p = m_playlists.atRow(row);
    if (!p) return;
    showEntries(p->id);
    m_openName = p->name;
    m_openId = p->id;
    m_openDynamic = p->kind == "dynamic";
    emit openChanged();
    m_openMatched = -1;  // counted in the background: the rules are matched
                         // against every track
    emit openMatchedChanged();
    if (p->kind == "dynamic")
        QThreadPool::globalInstance()->start(
            [this, id = p->id, path = m_dbPath] {
                const int n = countMatches(path, id);
                QMetaObject::invokeMethod(
                    this,
                    [this, id, n] {
                        if (id != m_openId || n == m_openMatched) return;
                        m_openMatched = n;
                        emit openMatchedChanged();
                    },
                    Qt::QueuedConnection);
            });
}

void Player::playEntry(int row) {
    const QVector<QueueItem> &items = m_entries.items();
    if (row < 0 || row >= items.size()) return;
    if (items[row].id == m_currentId) {
        resumeCurrent();
        return;
    }
    setPlayingList(m_openId);
    onEngine([e = m_engine, items, row, shuffle = shuffle(), id = m_openId] {
        e->playQueue(items, row, shuffle, id);
    });
}

void Player::nextSection() {
    onEngine([e = m_engine] { e->nextSection(); });
}

// The entries are shown at once; why a dynamic playlist chose each one needs
// the whole library (over a second on the phone), so it is worked out in the
// background and added to the rows when it is ready.
void Player::showEntries(qint64 id) {
    m_entries.setItems(playlistItems(m_dbPath, id));
    QList<qint64> ids;
    for (const QueueItem &t : m_entries.items()) ids << t.id;
    if (ids.isEmpty()) return;
    QThreadPool::globalInstance()->start([this, id, ids, path = m_dbPath] {
        const QHash<qint64, QString> why = explainTracks(path, id, ids);
        if (why.isEmpty()) return;
        QMetaObject::invokeMethod(
            this,
            [this, id, why] {
                if (id == m_openId) m_entries.setDetails(why);
            },
            Qt::QueuedConnection);
    });
}

// The plays of every song are worked out in the background (a pass over the
// library); the rows of the Songs tab that are on screen show them.
// The bars next to the playing song move to its beat.  The tag is read in the
// background; until it is known, and for a song without one, the beat is 100.
void Player::showBpm(qint64 id) {
    if (m_bpm != 100) {
        m_bpm = 100;
        emit bpmChanged();
    }
    QThreadPool::globalInstance()->start([this, id, path = m_dbPath] {
        double bpm = trackBpm(path, id);
        if (bpm <= 0) bpm = 100;
        bpm = std::clamp(bpm, 40.0, 240.0);
        QMetaObject::invokeMethod(
            this,
            [this, id, bpm] {
                if (id != m_currentId || bpm == m_bpm) return;
                m_bpm = bpm;
                emit bpmChanged();
            },
            Qt::QueuedConnection);
    });
}

void Player::refreshStats() {
    QThreadPool::globalInstance()->start([this, path = m_dbPath] {
        const QHash<qint64, SongStat> stats = songStats(path);
        QMetaObject::invokeMethod(
            this, [this, stats] { m_model.setStats(stats); },
            Qt::QueuedConnection);
    });
}

void Player::reloadPlaylists() {
    m_playlists.setItems(loadPlaylists(m_dbPath));
    countMatched();
    refreshStats();
    if (m_openId >= 0) showEntries(m_openId);
}
