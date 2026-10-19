// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
// Playback and the queue, in a thread of their own.  On Android Qt stops the
// main thread's event loop while the phone is locked or the app is in the
// background (the audio carries on, but nothing else runs), so the commands
// from the lock screen and headset, and the move to the next track, must not
// depend on the main thread.  The engine lives in a worker thread; the main
// thread (Player) only mirrors its state.
#pragma once
#include <QAudioOutput>
#include <QMediaPlayer>
#include <QElapsedTimer>
#include <QMutex>
#include <QObject>
#include <QTimer>
#include <QVector>
#include <atomic>

#include "playlists.h"
#include "trackmodel.h"

class Engine : public QObject {
    Q_OBJECT
public:
    std::atomic<qint64> position{0},
        duration{0};  // polled by the main thread (no event per tick)
    std::atomic<bool> playing{false};
    QString sectionText() const;  // "Warmup (1/3) — 4:12 left — 12:12 in all",
                                  // empty when no schedule plays

    explicit Engine(QObject *parent = nullptr) : QObject(parent) {}
    void handleCommand(int what,
                       qint64 argument);  // from the notification / lock screen
                                          // / headset buttons
public slots:
    void init();  // creates the player; runs in the worker thread
    void setRoot(const QString &root) { m_root = root; }
    void setDatabase(const QString &path);  // also tops up dynamic playlists
                                            // that are short, a little later
    void setMuted(bool on) { m_audio->setMuted(on); }
    void
    resume();  // play the current song if it is not playing; never restarts it
    void playQueue(const QVector<QueueItem> &items, int row, bool shuffle);
    void toggle();
    void playSections(const QVector<Section> &sections,
                      bool shuffle);  // a container or sequence
    void nextSection();
    void next();
    void previous();
    void seek(qint64 ms);
    void setShuffle(bool on);
    void setVolume(double volume, double target, bool leveling);
signals:
    void current(qint64 id, const QString &title, const QString &artist,
                 const QString &album);
    void playingChanged(bool playing);
    void message(const QString &text);
    void ratingKnown(qint64 id, int rating);  // -1: not rated
    void playCounted();  // a play was written to the database (lists may have
                         // changed)
public:
    void setRating(
        qint64 id,
        int rating);  // 0 to 5, or -1 for not rated; written to tags.db at once
private:
    void readRating(qint64 id);
    void refillLists();  // top up dynamic playlists that are short, in the
                         // background
    void buildOrder(int startRow, bool keepFirst);
    void playCurrent();
    void applyVolume();
    void startSection();
    void advanceSection();
    void endSchedule(const QString &why = {});
    void clockTick();
    bool scheduleNext();  // true when it moved on by itself
    void pushState();     // tell the background service what plays
    void stopService();
    void commitSkips();  // skipped songs leave the dynamic playlists
    void markPlayed(const QVector<qint64> &ids);  // mark these songs as played
                                                  // in every dynamic playlist
    void finishTrack();  // the counted track ended: take it off the dynamic
                         // playlists
    void
    countPlay();  // a play counts after 20 s of listening; written to tags.db

    QMediaPlayer *m_player = nullptr;
    QAudioOutput *m_audio = nullptr;
    QString m_root, m_dbPath;
    qint64 m_heard = 0;  // ms of this track actually played
    bool m_counted = false;
    QVector<qint64> m_skipped;  // left before counting; not yet taken off the
                                // dynamic playlists
    bool m_skippable = false;   // a song was started and not stopped since
    qint64 m_unmarkedId = -1;   // counted, still on its dynamic playlists
    QVector<QueueItem> m_queue,
        m_order;     // queue in list order; order in play order
    int m_pos = -1;  // index into m_order
    qint64 m_currentId = -1;
    QString m_title, m_artist;
    int m_rating = -1;  // of the current track: -1 unrated, 0..5 stars (shown
                        // in the notification)
    bool m_shuffle = false, m_leveling = true, m_measured = false;
    // Containers and sequences.  The clock counts only the time spent playing
    // and runs here, in the worker thread, so it keeps going while the phone is
    // locked.
    QVector<Section> m_sections;
    int m_section = -1;  // -1 = no schedule
    qint64 m_elapsed = 0;
    bool m_pending =
        false;  // "finish": the limit has passed, the track playing is the last
    double m_fade = 1.0;
    QTimer *m_clock = nullptr;
    QElapsedTimer m_lastTick;
    mutable QMutex m_textLock;
    QString m_sectionName;
    std::atomic<int> m_sectionCount{0};
    std::atomic<qint64> m_sectionRemaining{
        -1};  // ms left in this section, -1 = no limit
    std::atomic<qint64> m_totalRemaining{-1};  // ms left in all, -1 = unknown
    std::atomic<int> m_sectionIndex{0};

    double m_volume = 0.5, m_target = -18.0, m_loudness = 0;
};
