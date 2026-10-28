// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
// The part of the player the screen talks to: it forwards commands to the
// playback engine (which runs in its own thread, see engine.h) and mirrors the
// engine's state for the QML interface.
#pragma once
#include <QStringList>
#include <QVariantList>
#include <QVector>
#include <QObject>
#include <QSettings>
#include <QThread>
#include <QTimer>

#include "engine.h"
#include "playlists.h"
#include "sync.h"
#include "trackmodel.h"

class Player : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString title READ title NOTIFY currentChanged)
    Q_PROPERTY(QString artist READ artist NOTIFY currentChanged)
    Q_PROPERTY(QString album READ album NOTIFY currentChanged)
    Q_PROPERTY(qint64 currentId READ currentId NOTIFY currentChanged)
    Q_PROPERTY(double bpm READ bpm NOTIFY bpmChanged)  // of the song playing
    Q_PROPERTY(bool playing READ playing NOTIFY playingChanged)
    Q_PROPERTY(qint64 position READ position NOTIFY positionChanged)
    Q_PROPERTY(qint64 duration READ duration NOTIFY durationChanged)
    Q_PROPERTY(bool shuffle READ shuffle WRITE setShuffle NOTIFY shuffleChanged)
    Q_PROPERTY(bool shuffleAvailable READ shuffleAvailable NOTIFY
                   shuffleChanged)
    Q_PROPERTY(double volume READ volume WRITE setVolume NOTIFY volumeChanged)
    Q_PROPERTY(double target READ target WRITE setTarget NOTIFY volumeChanged)
    Q_PROPERTY(
        bool leveling READ leveling WRITE setLeveling NOTIFY volumeChanged)
    Q_PROPERTY(bool eqOn READ eqOn WRITE setEqOn NOTIFY eqChanged)
    Q_PROPERTY(QVariantList eqGains READ eqGains NOTIFY eqChanged)
    Q_PROPERTY(bool eqAuto READ eqAuto WRITE setEqAuto NOTIFY eqChanged)
    Q_PROPERTY(QString eqAutoName READ eqAutoName NOTIFY eqChanged)
    Q_PROPERTY(QStringList eqPresets READ eqPresets CONSTANT)
    Q_PROPERTY(QString eqPreset READ eqPreset NOTIFY eqChanged)
    Q_PROPERTY(QStringList eqCustomNames READ eqCustomNames NOTIFY eqChanged)
    Q_PROPERTY(QStringList eqBands READ eqBands CONSTANT)
    Q_PROPERTY(QVariantList eqShownGains READ eqShownGains NOTIFY eqChanged)
    Q_PROPERTY(QString eqShownPreset READ eqShownPreset NOTIFY eqChanged)
    Q_PROPERTY(QString eqSummary READ eqSummary NOTIFY eqChanged)
    Q_PROPERTY(QString dbPath READ dbPath NOTIFY pathsChanged)
    Q_PROPERTY(QString musicRoot READ musicRoot NOTIFY pathsChanged)
    Q_PROPERTY(QString message READ message NOTIFY messageChanged)
    Q_PROPERTY(QString build READ build CONSTANT)
    Q_PROPERTY(QString sectionText READ sectionText NOTIFY sectionChanged)
    Q_PROPERTY(QString openName READ openName NOTIFY openChanged)
    Q_PROPERTY(bool openDynamic READ openDynamic NOTIFY openChanged)
    Q_PROPERTY(int openMatched READ openMatched NOTIFY
                   openMatchedChanged)  // library tracks the open dynamic
                                        // playlist's rules select (-1: not
                                        // dynamic, or not counted yet)
    Q_PROPERTY(qint64 playingList READ playingList NOTIFY playingListChanged)
    Q_PROPERTY(int rating READ rating NOTIFY ratingChanged)
    Q_PROPERTY(bool syncRunning READ syncRunning NOTIFY syncChanged)
    Q_PROPERTY(QString syncCode READ syncCode NOTIFY syncChanged)
    Q_PROPERTY(QString syncAddresses READ syncAddresses NOTIFY syncChanged)
    Q_PROPERTY(QString syncStatus READ syncStatus NOTIFY syncChanged)
    Q_PROPERTY(
        bool needsFilesAccess READ needsFilesAccess NOTIFY filesAccessChanged)
public:
    explicit Player(QObject *parent = nullptr);
    ~Player() override;
    TrackModel *model() { return &m_model; }
    PlaylistModel *playlistModel() { return &m_playlists; }
    EntryModel *entryModel() { return &m_entries; }
    QString sectionText() const { return m_sectionText; }
    QString openName() const { return m_openName; }
    bool openDynamic() const { return m_openDynamic; }
    int openMatched() const { return m_openMatched; }
    qint64 playingList() const { return m_playingList; }
    int rating() const { return m_rating; }
    bool syncRunning() const { return m_syncRunning; }
    QString syncCode() const { return m_syncCode; }
    QString syncAddresses() const { return m_syncAddresses; }
    QString syncStatus() const { return m_syncStatus; }

    QString title() const { return m_title; }
    QString artist() const { return m_artist; }
    QString album() const { return m_album; }
    qint64 currentId() const { return m_currentId; }
    double bpm() const { return m_bpm; }
    bool playing() const { return m_engine->playing; }
    qint64 position() const { return m_engine->position; }
    // The bars: how loud the low (0), middle (1) and high (2) parts of the
    // sound are, 0 to 1, and whether they are being measured now (they are
    // when the sound goes through the equalizer path).  Polled by the bars.
    Q_INVOKABLE double level(int band) const;
    Q_INVOKABLE bool levelsLive() const;
    qint64 duration() const { return m_engine->duration; }
    // A dynamic playlist plays in its own order: shuffling it makes little
    // sense, so the shuffle is off (and cannot be switched on) while one plays.
    bool shuffle() const { return m_shuffle && !isDynamic(m_playingList); }
    bool shuffleAvailable() const { return !isDynamic(m_playingList); }
    double volume() const { return m_volume; }
    double target() const { return m_target; }
    bool leveling() const { return m_leveling; }
    bool eqOn() const { return m_eqOn; }
    bool eqAuto() const { return m_eqAuto; }
    // "": the song has no genre with a preset
    QString eqAutoName() const { return m_eqAutoName; }
    void setEqAuto(bool on);
    QVariantList eqGains() const;
    QStringList eqPresets() const;  // the built-in ones, then the saved ones
    QStringList eqCustomNames() const;
    QString eqPreset() const;  // the preset the gains match, else "Custom"
    QStringList eqBands() const;  // "60 Hz", ...
    // what is being applied: the song's automatic preset if there is one,
    // else the manual settings
    QVariantList eqShownGains() const;
    QString eqShownPreset() const;
    QString eqSummary() const;  // one line for the main screen
    void setEqOn(bool on);
    Q_INVOKABLE void setEqBand(int band, double gainDb);
    Q_INVOKABLE void setEqPreset(const QString &name);
    // saves the current settings under a name; returns "" or why it cannot
    Q_INVOKABLE QString saveEqPreset(const QString &name);
    Q_INVOKABLE void deleteEqPreset(const QString &name);
    QString dbPath() const { return m_dbPath; }
    QString musicRoot() const { return m_root; }
    QString message() const { return m_message; }
    QString build() const;
    bool needsFilesAccess() const { return m_needsFilesAccess; }
    void setShuffle(bool on);
    void setVolume(double v);
    void setTarget(double v);
    void setLeveling(bool on);

    void start();  // permission, paths, library
    Q_INVOKABLE void setPaths(const QString &db, const QString &root);
    Q_INVOKABLE void reload();
    Q_INVOKABLE void
    requestFilesAccess();  // opens the system screen where the user allows it
    void checkFilesAccess();
    Q_INVOKABLE void search(const QString &text) { m_model.setQuery(text); }
    Q_INVOKABLE void sortBy(const QString &key) { m_model.setSort(key); }
    Q_INVOKABLE void
    playRow(int row);  // queue = the list as shown, start at this row
    Q_INVOKABLE void
    playPlaylist(int row);  // a playlist, container or sequence as listed on
                            // the Playlists tab
    Q_INVOKABLE void openPlaylist(int row);  // show a playlist's tracks
    Q_INVOKABLE void
    playEntry(int row);  // play the open playlist from this track
    Q_INVOKABLE void nextSection();
    Q_INVOKABLE void rate(int stars);  // the playing track: 0 to 5 stars; the
                                       // star that is set again gives zero
    Q_INVOKABLE void clearRating();    // not rated
    Q_INVOKABLE void
    startSync();  // wait for the desktop (tools/sync_phone.py), see sync.h
    Q_INVOKABLE void stopSync();
    Q_INVOKABLE void newSyncCode();
    void reloadPlaylists();  // after a play was recorded: dynamic lists lose
                             // the track
    void showBpm(qint64 id);  // the beat of the bars
    void refreshStats();     // plays of each song, for the Songs tab
    void countMatched();     // library tracks matched by each dynamic playlist
                             // (background)
    void resumeCurrent();
    Q_INVOKABLE void moveToBack();  // the phone's Back at the top level: hide
                                    // the app, keep it (and the music) running
    Q_INVOKABLE void toggle();
    Q_INVOKABLE void next();
    Q_INVOKABLE void previous();
    Q_INVOKABLE void seek(qint64 ms);
signals:
    void currentChanged();
    void bpmChanged();
    void playingChanged();
    void positionChanged();
    void durationChanged();
    void shuffleChanged();
    void volumeChanged();
    void eqChanged();
    void pathsChanged();
    void messageChanged();
    void filesAccessChanged();
    void sectionChanged();
    void openChanged();
    void openMatchedChanged();
    void syncChanged();
    void ratingChanged();
    void playingListChanged();
    void
    syncFinished();  // the desktop has finished: the Settings dialog closes
private:
    template <class F> void onEngine(F f) {
        QMetaObject::invokeMethod(m_engine, std::move(f), Qt::QueuedConnection);
    }
    void say(const QString &text);
    void keepScreenOn(bool on);
    void pushVolume();
    void requestAudioPermission();
    QString defaultDataDir() const;
    QString findLibrary()
        const;  // <volume>/MusicLibrary, with tags.db next to the music

    TrackModel m_model;
    PlaylistModel m_playlists;
    EntryModel m_entries;
    QString m_sectionText, m_openName;
    bool m_openDynamic = false;  // rows keep room for the "why" line
    int m_openMatched = -1;
    void showEntries(qint64 id);
    qint64 m_openId = -1;  // the playlist shown on the Playlists tab
    QThread m_thread;
    Engine *m_engine;
    QThread m_syncThread;
    SyncServer *m_sync;
    bool m_syncRunning = false;
    QString m_syncCode, m_syncAddresses, m_syncStatus;
    QTimer m_poll;  // position and duration, a few times a second
    qint64 m_lastPosition = -1, m_lastDuration = -1;
    QSettings m_settings;
    QString m_dbPath, m_root, m_message;
    QString m_title, m_artist, m_album;
    qint64 m_currentId = -1;
    double m_bpm = 100;  // the song's BPM tag; 100 when it has none
    qint64 m_playingList = -1;  // the playlist, container or sequence the music
                                // comes from; -1 = the Songs tab
    void setPlayingList(qint64 id);
    bool isDynamic(qint64 playlistId) const;
    int m_rating = -1;  // of the playing track; -1 = not rated
    bool m_shuffle = false, m_leveling = true, m_needsFilesAccess = false;
    double m_volume = 1.0, m_target = -10.0;
    bool m_eqOn = false, m_eqAuto = false;
    QString m_eqAutoName;
    QVector<double> m_eqGains = QVector<double>(5, 0.0);
    QList<CustomEq> m_eqCustom;  // saved presets, by name
    void saveEqCustom();
    bool presetGains(const QString &name, QVector<double> &gains) const;
    void pushEq();
};
