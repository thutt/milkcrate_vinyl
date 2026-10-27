// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
#include "engine.h"
#include "dblock.h"
#include "dynamic.h"
#include <QAudioDevice>
#include <QThreadPool>
#include <QTimer>

#include <QDateTime>
#include <QFileInfo>
#include <QMutexLocker>
#include <QSqlDatabase>
#include <QSqlError>
#include <QSqlQuery>
#include <QUuid>
#include <QRandomGenerator>
#include <QUrl>
#include <algorithm>
#include <cmath>

#ifdef Q_OS_ANDROID
#include <QCoreApplication>
#include <QJniEnvironment>
#include <QJniObject>
#endif

void Engine::init() {
    m_writes.setMaxThreadCount(1);  // the writes keep their order
    m_player = new QMediaPlayer(this);
    m_audio = new QAudioOutput(this);
    m_player->setAudioOutput(m_audio);
    applyVolume();
    m_clock = new QTimer(this);
    m_clock->setInterval(250);
    connect(m_clock, &QTimer::timeout, this, &Engine::clockTick);
    connect(m_player, &QMediaPlayer::positionChanged, this,
            [this](qint64 p) { position = p; });
    connect(m_player, &QMediaPlayer::durationChanged, this,
            [this](qint64 d) {
                duration = d;
                if (playing) pushState();  // the time left needs the length
            });
    connect(m_player, &QMediaPlayer::playbackStateChanged, this,
            [this](QMediaPlayer::PlaybackState) {
                bool on =
                    m_player->playbackState() == QMediaPlayer::PlayingState;
                playing = on;
                if (m_player->playbackState() == QMediaPlayer::StoppedState)
                    finishTrack();
                if (on) {
                    m_lastTick.restart();
                    m_clock->start();
                } else
                    m_clock->stop();  // the clock runs only while music plays
                emit playingChanged(on);
                pushState();
            });
    connect(m_player, &QMediaPlayer::mediaStatusChanged, this,
            [this](QMediaPlayer::MediaStatus s) {
                if (s == QMediaPlayer::StalledMedia ||
                    s == QMediaPlayer::InvalidMedia)
                    qWarning("\"%s\": media status %d at %lld ms",
                             qPrintable(m_title), int(s), m_player->position());
                if (s != QMediaPlayer::EndOfMedia) return;
                m_naturalEnd = true;
                m_primed = true;  // the tail of the song needs no cushion
                next();
                m_naturalEnd = false;
            });
    m_bufferOut = new QAudioBufferOutput(this);
    connect(m_bufferOut, &QAudioBufferOutput::audioBufferReceived, this,
            &Engine::onAudioBuffer);
    m_drain = new QTimer(this);
    m_drain->setInterval(10);
    connect(m_drain, &QTimer::timeout, this, &Engine::drainSink);
    m_devices = new QMediaDevices(this);
    connect(m_devices, &QMediaDevices::audioOutputsChanged, this,
            [this] { m_devicesChanged = true; });
    connect(m_player, &QMediaPlayer::errorOccurred, this,
            [this](QMediaPlayer::Error, const QString &text) {
                qWarning("\"%s\": cannot play: %s", qPrintable(m_title),
                         qPrintable(text));
                emit message("Cannot play: " + text);
            });
}

// The slider sets the volume; leveling scales it by the difference between the
// target and the track's loudness (the output cannot go above full).
void Engine::applyVolume() {
    double gain = (m_leveling && m_measured) ? m_target - m_loudness : 0.0;
    double v = std::min(1.0, m_volume * m_fade * std::pow(10.0, gain / 20.0));
    m_audio->setVolume(v);
    m_gain = m_muted ? 0.0f : float(v);  // used before the equalizer
}

void Engine::setMuted(bool on) {
    m_muted = on;
    m_audio->setMuted(on);
    applyVolume();
}

// -- the equalizer --

void Engine::setEqualizer(bool on, const QVector<double> &gains,
                          bool automatic, const QList<CustomEq> &customs) {
    m_eqOn = on;  // takes effect with the next song
    m_eqGains = gains;
    m_eqGains.resize(Equalizer::Bands);
    const bool changed = customs.size() != m_customs.size() ||
                         [&] {
                             for (int i = 0; i < customs.size(); ++i)
                                 if (customs[i].name != m_customs[i].name ||
                                     customs[i].gains != m_customs[i].gains)
                                     return true;
                             return false;
                         }();
    m_customs = customs;
    if (automatic != m_eqAuto || (automatic && changed)) {
        m_eqAuto = automatic;
        chooseEqPreset(m_currentId);
    }
    applyEqGains();
}

// The song keeps playing through the sink after the equalizer is turned off
// (the player changes its output only when the next song is loaded), with flat
// gains.
const double *Engine::eqGains() const {
    static const double flat[Equalizer::Bands] = {0, 0, 0, 0, 0};
    if (!m_eqOn) return flat;
    return m_eqAuto && m_autoChosen ? m_autoGains.constData()
                                    : m_eqGains.constData();
}

void Engine::applyEqGains() {
    if (m_sourceRate > 0) m_eq.configure(m_sourceRate, eqGains());
}

void Engine::chooseEqPreset(qint64 id) {
    m_autoChosen = false;
    m_autoName.clear();
    if (m_eqAuto && id >= 0) {
        const QStringList genres = readGenres(id);
        const int custom = eqCustomForGenres(m_customs, genres);
        if (custom >= 0) {
            m_autoName = m_customs[custom].name;
            m_autoGains = m_customs[custom].gains;
            m_autoGains.resize(5);
            m_autoChosen = true;
        } else if (const EqPreset *p = eqPresetForGenres(genres)) {
            m_autoName = p->name;
            m_autoGains = QVector<double>(p->gains, p->gains + 5);
            m_autoChosen = true;
        }
    }
    emit eqChosen(m_autoName);
}

// The engine thread also feeds the sound; time spent waiting here is time the
// sound is not fed.  Only the code that writes the database, or replaces the
// file, holds dbLock(); the engine's reads (genres, rating, kind of list) take
// no lock, because a long rule evaluation in another thread kept the engine
// waiting for over a second at the start of a song, and the sound stuttered.
static void noteWait(const QElapsedTimer &since, const char *what) {
    if (since.elapsed() > 50)
        qWarning("engine thread waited %lld ms for the database (%s)",
                 since.elapsed(), what);
}

QStringList Engine::readGenres(qint64 id) {
    QStringList genres;
    if (m_dbPath.isEmpty()) return genres;
    const QString name = "genre-" + QUuid::createUuid().toString(QUuid::Id128);
    {
        QSqlDatabase db = QSqlDatabase::addDatabase("QSQLITE", name);
        db.setDatabaseName(m_dbPath);
        db.setConnectOptions("QSQLITE_OPEN_READONLY;QSQLITE_BUSY_TIMEOUT=3000");
        if (db.open()) {
            QSqlQuery q(db);
            q.prepare("SELECT value FROM tag WHERE file_id = ? AND name = "
                      "'GENRE' ORDER BY pos");
            q.addBindValue(id);
            if (q.exec())
                while (q.next()) genres << q.value(0).toString();
            db.close();
        }
    }
    QSqlDatabase::removeDatabase(name);
    return genres;
}

void Engine::routeAudio() {
    if (m_eqOn == m_eqRouted) return;
    if (m_eqOn) {
        m_player->setAudioOutput(nullptr);
        m_player->setAudioBufferOutput(m_bufferOut);
        m_sinceDrain.invalidate();
        m_drain->start();
    } else {
        m_player->setAudioBufferOutput(nullptr);
        m_player->setAudioOutput(m_audio);
        m_drain->stop();
        m_sinceDrain.invalidate();
        closeSink();
    }
    m_eqRouted = m_eqOn;
}

void Engine::closeSink() {
    if (m_sink) {
        m_sink->stop();
        delete m_sink;
        m_sink = nullptr;
        m_sinkIo = nullptr;
    }
    m_eqQueue.clear();
    m_sourceRate = 0;
}

void Engine::flushAudio() {
    QElapsedTimer took;
    took.start();
    m_primed = false;
    m_sincePrime.restart();
    m_sinceBuffer.invalidate();
    m_eqQueue.clear();
    m_eq.reset();
    m_resamplePos = 0;
    m_last[0] = m_last[1] = 0;
    // A new sink (and with it a new AudioTrack) for each song, as the player's
    // own output does.  One sink stopped and started again for each song kept a
    // stutter, which a restart of the song cleared, after a few quick changes
    // of song; it is opened with the first buffer (onAudioBuffer).
    closeSink();
    if (took.elapsed() > 20)
        qWarning("closing the sound output took %lld ms", took.elapsed());
    dropStale();
}

void Engine::dropStale() {
    ++m_staleMarks;
    QMetaObject::invokeMethod(
        this,
        [this] {
            if (--m_staleMarks > 0) return;
            if (m_dropped > 0)
                qInfo("dropped %d buffers of the song before", m_dropped);
            m_dropped = 0;
        },
        Qt::QueuedConnection);
}

// Stereo 16 bit at the source's rate when the device takes it, else at the
// device's preferred rate (the sound is resampled in onAudioBuffer).
void Engine::openSink(int sourceRate) {
    closeSink();
    QAudioDevice device = QMediaDevices::defaultAudioOutput();
    QAudioFormat format;
    format.setSampleFormat(QAudioFormat::Int16);
    format.setChannelCount(2);
    format.setSampleRate(sourceRate);
    if (!device.isFormatSupported(format))
        format.setSampleRate(device.preferredFormat().sampleRate());
    m_sink = new QAudioSink(device, format, this);
    // an underrun: the device wanted sound and the queue had none
    connect(m_sink, &QAudioSink::stateChanged, this, [this](QAudio::State s) {
        if (s != QAudio::IdleState || !m_sink) return;
        qint64 pos = m_player->position(), left = m_player->duration() - pos;
        if (m_player->playbackState() != QMediaPlayer::PlayingState ||
            pos <= 0 || left < 500 || !m_sinceBuffer.isValid() || !m_primed)
            return;  // a new sink is idle until the first sound is given
        qWarning("sound output ran dry at %lld ms: %d bytes queued, last "
                 "buffer %lld ms ago", pos, int(m_eqQueue.size()),
                 m_sinceBuffer.isValid() ? m_sinceBuffer.elapsed() : -1);
        m_primed = false;  // build up the cushion again
        m_sincePrime.restart();
    });
    m_sink->setBufferSize(int(format.bytesForDuration(150000)));
    m_sinkIo = m_sink->start();
    m_sourceRate = sourceRate;
    m_sinkRate = format.sampleRate();
    m_devicesChanged = false;
    qInfo("sound output \"%s\": %d Hz asked for, %d Hz used, device "
          "prefers %d Hz, buffer %d bytes", qPrintable(device.description()),
          sourceRate, format.sampleRate(),
          device.preferredFormat().sampleRate(), int(m_sink->bufferSize()));
    m_resamplePos = 0;
    m_eq.configure(sourceRate, eqGains());
    m_eq.reset();
}

void Engine::drainSink() {
    if (m_sinceDrain.isValid() && m_sinceDrain.elapsed() > 150)
        qWarning("engine thread did not feed the sound for %lld ms",
                 m_sinceDrain.elapsed());
    m_sinceDrain.start();
    if (!m_sinkIo || m_eqQueue.isEmpty()) return;
    if (!m_primed) {
        if (m_eqQueue.size() < m_sink->format().bytesForDuration(300000))
            return;
        m_primed = true;
        qInfo("sound output started %lld ms after the song, %lld ms after it "
              "was restarted, %d bytes queued", m_sinceSource.elapsed(),
              m_sincePrime.elapsed(), int(m_eqQueue.size()));
    }
    qint64 room = m_sink->bytesFree();
    if (room <= 0) return;
    qint64 n = m_sinkIo->write(m_eqQueue.constData(),
                               qMin<qint64>(room, m_eqQueue.size()));
    if (n > 0) m_eqQueue.remove(0, int(n));
}

static float sampleAt(const QAudioBuffer &b, int frame, int channel) {
    int n = b.format().channelCount();
    int i = frame * n + (channel < n ? channel : n - 1);
    switch (b.format().sampleFormat()) {
        case QAudioFormat::UInt8:
            return (b.constData<quint8>()[i] - 128) / 128.0f;
        case QAudioFormat::Int16: return b.constData<qint16>()[i] / 32768.0f;
        case QAudioFormat::Int32:
            return float(b.constData<qint32>()[i] / 2147483648.0);
        case QAudioFormat::Float: return b.constData<float>()[i];
        default: return 0;
    }
}

void Engine::onAudioBuffer(const QAudioBuffer &buffer) {
    if (!m_eqRouted || !buffer.isValid()) return;
    if (m_staleMarks > 0) {
        ++m_dropped;
        return;
    }
    if (m_firstBuffer) {
        m_firstBuffer = false;
        qInfo("\"%s\": first sound after %lld ms", qPrintable(m_title),
              m_sinceSource.elapsed());
    }
    else if (m_sinceBuffer.isValid() && m_sinceBuffer.elapsed() > 200)
        qWarning("sound buffer came %lld ms after the one before",
                 m_sinceBuffer.elapsed());
    m_sinceBuffer.restart();
    int rate = buffer.format().sampleRate(), frames = buffer.frameCount();
    if (!m_sink || rate != m_sourceRate || m_devicesChanged) {
        qInfo("opening the sound output: %s",
              !m_sink                ? "none was open"
              : rate != m_sourceRate ? "the song's rate changed"
                                     : "the output devices changed");
        QElapsedTimer took;
        took.start();
        openSink(rate);
        qInfo("opening the sound output took %lld ms", took.elapsed());
    }
    if (!m_sinkIo) return;
    // stereo float, with the volume (ramped across the buffer, not stepped)
    QVector<float> pcm(frames * 2);
    float from = m_gainFrom, to = m_gain;
    for (int f = 0; f < frames; ++f) {
        float g = from + (to - from) * float(f) / float(frames);
        pcm[f * 2] = sampleAt(buffer, f, 0) * g;
        pcm[f * 2 + 1] = sampleAt(buffer, f, 1) * g;
    }
    m_gainFrom = to;
    m_eq.process(pcm.data(), frames);
    // resample when the device does not take the source's rate (linear)
    QVector<float> out;
    if (m_sinkRate == rate) {
        out = pcm;
    } else {
        double step = double(rate) / m_sinkRate;
        double pos = m_resamplePos;  // 0 is the sample before pcm[0]
        while (pos < frames) {
            int i = int(std::floor(pos));
            double frac = pos - i;
            for (int c = 0; c < 2; ++c) {
                float a = i == 0 ? m_last[c] : pcm[(i - 1) * 2 + c];
                float b = pcm[i * 2 + c];
                out.append(float(a + (b - a) * frac));
            }
            pos += step;
        }
        m_resamplePos = pos - frames;
        m_last[0] = pcm[(frames - 1) * 2];
        m_last[1] = pcm[(frames - 1) * 2 + 1];
    }
    QByteArray bytes(out.size() * 2, Qt::Uninitialized);
    qint16 *dst = reinterpret_cast<qint16 *>(bytes.data());
    for (int i = 0; i < out.size(); ++i)
        dst[i] = qint16(std::lrint(std::clamp(out[i], -1.0f, 1.0f) * 32767));
    m_eqQueue.append(bytes);
    const int maxQueue = m_sinkRate * 4 * 2;  // 2 s: the device has stalled
    if (m_eqQueue.size() > maxQueue)
        m_eqQueue.remove(0, m_eqQueue.size() - maxQueue);
    drainSink();
}

void Engine::setVolume(double volume, double target, bool leveling) {
    m_volume = volume;
    m_target = target;
    m_leveling = leveling;
    applyVolume();
}

// The play order: the queue as it is, or shuffled with the current track first
// (every track once per round).
void Engine::buildOrder(int startRow, bool keepFirst) {
    m_order = m_queue;
    if (m_order.isEmpty()) {
        m_pos = -1;
        return;
    }
    if (!m_shuffle) {
        m_pos = std::clamp(startRow, 0, int(m_order.size()) - 1);
        return;
    }
    qint64 first = (keepFirst || startRow >= 0) && startRow < m_queue.size()
                       ? m_queue[std::max(startRow, 0)].id
                       : -1;
    std::shuffle(m_order.begin(), m_order.end(), *QRandomGenerator::global());
    if (first >= 0) {
        auto it =
            std::find_if(m_order.begin(), m_order.end(),
                         [first](const QueueItem &q) { return q.id == first; });
        if (it != m_order.end()) std::rotate(m_order.begin(), it, it + 1);
    }
    m_pos = 0;
}

void Engine::playQueue(const QVector<QueueItem> &items, int row, bool shuffle,
                       qint64 listId) {
    if (row < 0 || row >= items.size()) return;
    qInfo("song chosen from a list: row %d of %d, shuffle %s, at %lld ms of "
          "the song playing", row + 1, int(items.size()), shuffle ? "on" : "off",
          m_player->position());
    endSchedule();
    m_listId = listId;
    m_queue = items;
    m_shuffle = shuffle;
    buildOrder(row, true);
    m_visited.clear();
    m_follow = listIsDynamic();
    if (m_follow) {  // the songs after this one are chosen from the database
        const QueueItem first = m_order[m_pos];
        m_order.clear();
        m_order.append(first);
        m_pos = 0;
    }
    playCurrent();
}

void Engine::setShuffle(bool on) {
    if (on == m_shuffle) return;
    m_shuffle = on;
    if (m_queue.isEmpty() || m_follow) return;  // the next choice follows it
    int row = 0;
    for (int i = 0; i < m_queue.size(); ++i)
        if (m_queue[i].id == m_currentId) {
            row = i;
            break;
        }
    buildOrder(row, true);
}

void Engine::playCurrent() {
    if (m_pos < 0 || m_pos >= m_order.size()) return;
    m_queueEnded = false;
    const QueueItem &t = m_order[m_pos];
    QElapsedTimer total;  // where the time goes when a song is started
    total.start();
    QString laps;
    qint64 lapped = 0;
    auto lap = [&](const char *what) {
        qint64 now = total.elapsed();
        laps += QString(", %1 %2").arg(what).arg(now - lapped);
        lapped = now;
    };
    finishTrack();
    lap("finishing the last");
    if (m_currentId >= 0 && m_currentId != t.id && m_skippable && !m_counted &&
        !m_skipped.contains(m_currentId))
        m_skipped.append(m_currentId);  // left before it counted: a skip, which
                                        // takes effect later
    m_skipped.removeAll(t.id);  // back to a skipped song: the skip is cancelled
    m_skippable = true;
    m_currentId = t.id;
    m_visited.insert(t.id);
    qInfo("song %d of %d (id %lld, queue of %d from list %lld): %s - %s",
          int(m_pos + 1), int(m_order.size()), t.id, int(m_queue.size()),
          m_listId, qPrintable(t.artist), qPrintable(t.title));
    m_heard = 0;
    m_counted = false;
    m_title = t.title;
    m_artist = t.artist;
    m_album = t.album;
    m_phase = 0;  // a new song starts with its title
    m_measured = t.measured;
    m_loudness = t.loudness;
    applyVolume();
    qInfo("volume: leveling %s, target %.1f LUFS, song %s%.1f LUFS, output "
          "gain %.3f", m_leveling ? "on" : "off", m_target,
          m_measured ? "" : "unmeasured ", m_measured ? m_loudness : 0.0,
          m_gain);
    emit current(t.id, t.title, t.artist, t.album);
    m_rating = -1;
    lap("telling the screen");
    readRating(t.id);
    lap("rating");
    QString full = m_root + "/" + cleanPath(t.path);
    if (!QFileInfo::exists(full)) {
        m_player->stop();
        m_player->setSource({});
        QString up = full;  // the deepest part of the path that exists
        while (up.size() > 1 && !QFileInfo::exists(up))
            up.truncate(up.lastIndexOf('/'));
        emit message("Not on this device: " + full + "\nFound up to: " + up);
        return;  // stay on this track so the message stays readable
    }
    emit message({});
    m_stalledMs = 0;
    m_kicks = 0;
    lap("file check");
    chooseEqPreset(t.id);
    applyEqGains();
    lap("genres");
    routeAudio();
    lap("routing");
    if (!m_naturalEnd) flushAudio();  // else the last of the song still plays
    lap("flush");
    m_firstBuffer = m_eqOn;
    m_sinceBuffer.invalidate();
    m_sinceSource.start();
    m_player->setSource(QUrl::fromLocalFile(full));
    lap("set source");
    m_player->play();
    if (!m_naturalEnd) dropStale();  // what the old song still delivers
    lap("play");
    qInfo("song change took %lld ms (%lld ms choosing the song before it)%s",
          total.elapsed(), m_choiceMs, qPrintable(laps));
    m_choiceMs = 0;
}

void Engine::resume() {
    if (m_currentId >= 0 &&
        m_player->playbackState() != QMediaPlayer::PlayingState)
        toggle();
}

void Engine::toggle() {
    if (m_currentId < 0) return;
    if (m_queueEnded) {  // Play after the end: the list from the top
        if (m_listId >= 0 && listIsDynamic()) {
            continueFromList(true);
            return;
        }
        buildOrder(0, true);
        playCurrent();
        return;
    }
    if (m_player->playbackState() == QMediaPlayer::PlayingState) {
        qInfo("paused at %lld ms", m_player->position());
        m_player->pause();
        return;
    }
    if (m_player->source().isEmpty() ||
        m_player->mediaStatus() == QMediaPlayer::NoMedia ||
        m_player->mediaStatus() == QMediaPlayer::InvalidMedia) {
        playCurrent();
        return;
    }
    qInfo("resumed at %lld ms", m_player->position());
    m_sinceBuffer.invalidate();  // a pause is not a late buffer
    m_primed = false;
    m_sincePrime.restart();
    QElapsedTimer took;
    took.start();
    m_player->play();
    qInfo("play after a pause took %lld ms", took.elapsed());
}

void Engine::next() {
    if (m_order.isEmpty()) return;
    qInfo("next song asked for (%s) at %lld ms",
          m_naturalEnd ? "the song ended" : "button or list",
          m_player->position());
    if (m_section >= 0 && scheduleNext()) return;
    if (m_pos + 1 < m_order.size())
        ++m_pos;
    else if (m_follow) {
        QElapsedTimer choice;
        choice.start();
        const bool found = followNext();
        m_choiceMs = choice.elapsed();
        if (!found) {
            // nothing on the list is left to play: the played songs have left
            // it and others are coming in, so look again shortly
            qInfo("nothing left to play on the playlist: reading it again");
            m_skippable = false;
            m_queueEnded = true;
            m_endClock.start();
            m_player->stop();
            pushState(true);  // paused: the service and its controls stay
            QTimer::singleShot(3000, this, [this] { continueFromList(); });
            return;
        }
    } else if (m_shuffle)
        buildOrder(-1, false);  // a new round
    else {
        qInfo("end of the queue (%d songs, shuffle off): waiting",
              int(m_order.size()));
        m_skippable = false;
        m_player->stop();
        m_queueEnded = true;
        pushState(true);  // paused: the service and its controls stay
        return;
    }  // end of the queue
    playCurrent();
}

// The song after this one comes from the playlist as the database holds it
// now, so the screen and the player agree on what is next: in order, the
// first song after this one's place, or any song not yet played if shuffling.
// False: none is left.
bool Engine::followNext() {
    m_queue = playlistItems(m_dbPath, m_listId);
    const qint64 here = m_order[m_pos].pos;
    QVector<const QueueItem *> open;
    for (const QueueItem &t : m_queue)
        if (!m_visited.contains(t.id)) open.append(&t);
    const QueueItem *pick = nullptr;
    if (m_shuffle && !open.isEmpty())
        pick = open[int(
            QRandomGenerator::global()->bounded(quint32(open.size())))];
    else if (!m_shuffle)
        for (const QueueItem *t : open)
            if (t->pos > here) {
                pick = t;
                break;
            }
    if (!pick) return false;
    m_order.append(*pick);
    ++m_pos;
    return true;
}

bool Engine::listIsDynamic() const {
    if (m_listId < 0 || m_dbPath.isEmpty()) return false;
    bool dynamic = false;
    const QString name = "kind-" + QUuid::createUuid().toString(QUuid::Id128);
    {
        QSqlDatabase db = QSqlDatabase::addDatabase("QSQLITE", name);
        db.setDatabaseName(m_dbPath);
        db.setConnectOptions("QSQLITE_OPEN_READONLY;QSQLITE_BUSY_TIMEOUT=3000");
        if (db.open()) {
            QSqlQuery q(db);
            q.prepare("SELECT rules IS NOT NULL FROM playlist WHERE id = ?");
            q.addBindValue(m_listId);
            dynamic = q.exec() && q.next() && q.value(0).toBool();
            db.close();
        }
    }
    QSqlDatabase::removeDatabase(name);
    return dynamic;
}

// The last song of the queue has gone off the dynamic playlist and the list
// has been topped up (refillLists, or the timer that started this): play what
// the list holds now, or wait if it holds nothing.
void Engine::continueFromList(bool user) {
    if (!m_queueEnded || m_listId < 0) return;
    if (!user && (!m_endClock.isValid() || m_endClock.elapsed() > 15000))
        return;
    const QVector<QueueItem> items = playlistItems(m_dbPath, m_listId);
    if (items.isEmpty()) {
        qInfo("the playlist has no songs left: waiting");
        return;
    }
    qInfo("the playlist now holds %d songs: playing them", int(items.size()));
    m_queue = items;
    m_visited.clear();  // a new round
    buildOrder(m_shuffle ? int(QRandomGenerator::global()->bounded(
                               quint32(items.size())))
                         : 0,
               true);
    const QueueItem first = m_order[m_pos];
    m_order.clear();
    m_order.append(first);
    m_pos = 0;
    playCurrent();
}

void Engine::previous() {
    if (m_order.isEmpty()) return;
    if (m_player->position() > 3000 || m_pos == 0) {
        qInfo("previous: back to the start of the song, from %lld ms",
              m_player->position());
        m_player->setPosition(0);
        flushAudio();
        return;
    }
    --m_pos;
    playCurrent();
}

void Engine::seek(qint64 ms) {
    qInfo("seek from %lld ms to %lld ms", m_player->position(), ms);
    QElapsedTimer took;
    took.start();
    m_player->setPosition(ms);
    qint64 player = took.elapsed();
    flushAudio();
    qInfo("seek took %lld ms (player %lld ms)", took.elapsed(), player);
    pushState();
}

// Commands from PlaybackService (what: 0 play, 1 pause, 2 next, 3 previous, 4
// stop, 5 seek, 6 one star less (0 stars: unrated), 7 one star more (unrated: 0
// stars)).
void Engine::handleCommand(int what, qint64 argument) {
    enum { Play, Pause, Next, Previous, Stop, Seek, StarDown, StarUp };
    bool on = m_player->playbackState() == QMediaPlayer::PlayingState;
    switch (what) {
        case Play:
            if (!on) toggle();
            break;
        case Pause:
            if (on) {
                qInfo("paused from outside the app at %lld ms",
                      m_player->position());
                m_player->pause();
            }
            break;
        case Next: next(); break;
        case Previous: previous(); break;
        case Stop:
            qInfo("stop requested");
            endSchedule();
            m_skippable = false;
            m_player->stop();
            stopService();
            break;
        case Seek: seek(argument); break;
        case StarDown:
            if (m_currentId >= 0 && m_rating >= 0)
                setRating(m_currentId, m_rating - 1);
            break;  // 1 star becomes 0 stars, 0 stars becomes unrated
        case StarUp:
            if (m_currentId >= 0 && m_rating < 5)
                setRating(m_currentId, m_rating + 1);
            break;  // unrated becomes 0 stars
    }
}

// The background service shows the track and the play state in the notification
// and on the lock screen.
void Engine::pushState(bool force) {
    m_sincePush = 0;
#ifdef Q_OS_ANDROID
    if (m_currentId < 0 ||
        (m_player->playbackState() == QMediaPlayer::StoppedState && !force))
        return;
    const bool playing =
        m_player->playbackState() == QMediaPlayer::PlayingState;
    QJniObject context = QNativeInterface::QAndroidApplication::context();
    QJniObject::callStaticMethod<void>(
        "org/musicplayer/player/PlaybackService", "update",
        "(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;"
        "Ljava/lang/String;ZJJII)V",
        context.object(), QJniObject::fromString(m_title).object<jstring>(),
        QJniObject::fromString(m_artist).object<jstring>(),
        QJniObject::fromString(m_album).object<jstring>(), jboolean(playing),
        jlong(m_player->position()), jlong(m_player->duration()),
        jint(m_rating), jint(playing ? m_phase : 0));
#endif
}

void Engine::stopService() {
#ifdef Q_OS_ANDROID
    QJniObject::callStaticMethod<void>(
        "org/musicplayer/player/PlaybackService", "stop",
        "(Landroid/content/Context;)V",
        QNativeInterface::QAndroidApplication::context().object());
#endif
}

// -- containers and sequences (see file-tags/containers.py, which plays the
// same way on the desktop) --

void Engine::playSections(const QVector<Section> &sections, bool shuffle) {
    endSchedule();
    if (sections.isEmpty()) {
        emit message("Nothing to play: it has no containers.");
        return;
    }
    m_sections = sections;
    m_sectionCount = sections.size();
    m_shuffle = shuffle;
    m_section = 0;
    startSection();
}

// Begin the current section: its tracks, a fresh clock, full volume.  Sections
// without tracks are skipped.
void Engine::startSection() {
    while (m_section >= 0 && m_section < m_sections.size()) {
        const Section &s = m_sections[m_section];
        if (!s.items.isEmpty()) {
            m_queue = s.items;
            m_follow = false;
            m_elapsed = 0;
            m_pending = false;
            if (m_fade != 1.0) {
                m_fade = 1.0;
                applyVolume();
            }
            {
                QMutexLocker lock(&m_textLock);
                m_sectionName = s.name;
            }
            m_sectionIndex = m_section;
            m_sectionRemaining = s.limitMs;
            qint64 total = s.limitMs;
            for (int i = m_section + 1; i < m_sections.size() && total >= 0;
                 ++i)
                total = m_sections[i].limitMs < 0
                            ? -1
                            : total + m_sections[i].limitMs;
            m_totalRemaining = total;
            m_lastTick.restart();
            buildOrder(m_shuffle ? int(QRandomGenerator::global()->bounded(
                                       quint32(m_queue.size())))
                                 : 0,
                       true);
            playCurrent();
            return;
        }
        ++m_section;
    }
    endSchedule();
    m_skippable = false;  // stopped, not skipped
    m_player->stop();
    stopService();
    emit message("Nothing to play.");
}

void Engine::advanceSection() {
    if (m_section < 0) return;
    ++m_section;
    if (m_section < m_sections.size()) {
        startSection();
        return;
    }
    endSchedule();
    m_skippable = false;  // stopped, not skipped
    m_player->stop();
    stopService();
    emit message("Sequence complete.");
}

void Engine::nextSection() {
    if (m_section >= 0) advanceSection();
}

void Engine::endSchedule(const QString &) {
    m_section = -1;
    m_sections.clear();
    m_sectionCount = 0;
    m_pending = false;
    if (m_fade != 1.0) {
        m_fade = 1.0;
        if (m_audio) applyVolume();
    }
}

// The track ended or Next was pressed: true if the schedule moved on (next
// section, or the section's lists again).
bool Engine::scheduleNext() {
    if (m_pending) {
        advanceSection();
        return true;
    }  // "finish": the limit passed and this track is over
    if (m_pos + 1 < m_order.size()) return false;  // more tracks in this round
    if (m_sections[m_section].whenEmpty == "next") {
        advanceSection();
        return true;
    }
    buildOrder(m_shuffle ? int(QRandomGenerator::global()->bounded(
                               quint32(m_queue.size())))
                         : 0,
               true);
    playCurrent();  // "repeat": the section's lists again
    return true;
}

// Every 250 ms: count the time that was played, fade out near the limit, move
// on when it is reached.
// Some files never start: the player says it plays, the position stays at 0 and
// no sound comes.  So far these were MP3 files with both a cover picture and a
// LAME/Xing header (the sound starts 25 ms in, the picture too).  Moving the
// position, to 0 as well, makes the player read the file again and it plays.
void Engine::kickStalledStart(qint64 dt) {
    if (m_player->position() > 0) {
        m_stalledMs = 0;
        return;
    }
    m_stalledMs += dt;
    if (m_stalledMs < 1500 || m_kicks >= 3) return;
    ++m_kicks;
    m_stalledMs = 0;
    qWarning("\"%s\" did not start: moving to the start (try %d)",
             qPrintable(m_title), m_kicks);
    m_player->setPosition(0);
    flushAudio();
}

void Engine::clockTick() {
    qint64 dt = std::min<qint64>(m_lastTick.restart(), 1000);
    if (!playing) return;
    // the time left in the notification and on the car's display is text
    m_sincePush += dt;
    if (m_sincePush >= 10000) {
        m_phase ^= 1;  // a head unit with one line shows the title, then the
                       // stars and the time left
        pushState();
    }
    kickStalledStart(dt);
    if (m_player->position() == 0) return;  // not heard yet
    m_heard += dt;
    if (!m_counted && m_heard >= 20000 && m_currentId >= 0) {
        m_counted = true;
        countPlay();
        commitSkips();
    }
    if (m_section < 0) return;
    m_elapsed += dt;
    const Section &s = m_sections[m_section];
    qint64 remaining =
        s.limitMs < 0 ? -1 : std::max<qint64>(0, s.limitMs - m_elapsed);
    m_sectionRemaining = remaining;
    if (m_totalRemaining >= 0) {
        qint64 later = 0;
        for (int i = m_section + 1; i < m_sections.size(); ++i)
            later += m_sections[i].limitMs;
        m_totalRemaining = remaining + later;
    }
    if (s.limitMs < 0 || m_pending) return;
    if (remaining <= 0) {
        if (s.endMode == "finish")
            m_pending = true;
        else
            advanceSection();
        return;
    }
    double fade =
        (s.endMode == "fade" && remaining < 3000) ? remaining / 3000.0 : 1.0;
    if (fade != m_fade) {
        m_fade = fade;
        applyVolume();
    }
}

QString Engine::sectionText() const {
    int count = m_sectionCount;
    if (count == 0) return {};
    QString name;
    {
        QMutexLocker lock(&m_textLock);
        name = m_sectionName;
    }
    qint64 remaining = m_sectionRemaining, total = m_totalRemaining;
    QString text =
        QString("%1 (%2/%3)").arg(name).arg(m_sectionIndex + 1).arg(count);
    text += remaining < 0 ? " — no limit"
                          : " — " + formatClock(remaining) + " left";
    if (count > 1 && total >= 0) text += " — " + formatClock(total) + " in all";
    return text;
}

// A play counts after 20 s of listening (as on the desktop): the track's play
// count and last-played time are written to tags.db (stats_modified says when,
// so the sync keeps the newer write).  The track stays on its dynamic playlists
// until it ends or is left (finishTrack).  Own short-lived connection, rollback
// journal: no write-ahead log on the card.
//
// The writes wait for the card (a full sync each), so they run in the writer
// thread, one after the other; the engine thread also feeds the sound, and a
// write of 100 ms or more there was heard as a stutter when a song started.
void Engine::countPlay() {
    if (m_dbPath.isEmpty()) return;
    const qint64 id = m_currentId;
    m_unmarkedId = id;  // marking it played is queued after this write
    m_writes.start([this, path = m_dbPath, id] { writePlay(path, id); });
}

void Engine::writePlay(const QString &path, qint64 id) {
    QElapsedTimer waiting;
    waiting.start();
    QMutexLocker lock(
        &dbLock());  // the sync server may be replacing the database
    noteWait(waiting, "play count");
    const QString name = "play-" + QUuid::createUuid().toString(QUuid::Id128);
    QString problem;
    {
        QSqlDatabase db = QSqlDatabase::addDatabase("QSQLITE", name);
        db.setDatabaseName(path);
        db.setConnectOptions("QSQLITE_BUSY_TIMEOUT=3000");
        if (!db.open()) {
            problem = db.lastError().text();
        } else {
            QSqlQuery q(db);
            auto hasColumn = [&q](const QString &table, const QString &column) {
                q.exec("PRAGMA table_info(" + table + ")");
                while (q.next())
                    if (q.value(1).toString() == column) return true;
                return false;
            };
            if (!hasColumn("media_file", "stats_modified") ||
                !hasColumn("playlist_item", "removed_at")) {
                problem = "The database is from an older version: sync with "
                          "the desktop to record plays.";
            } else {
                const QDateTime now = QDateTime::currentDateTimeUtc();
                const QString stamp = now.toString("yyyy-MM-ddTHH:mm:ss.zzzZ"),
                              last = now.toString("yyyy-MM-ddTHH:mm:ssZ");
                q.exec("PRAGMA journal_mode = DELETE");
                q.exec("PRAGMA synchronous = FULL");
                bool ok = db.transaction();
                q.prepare("UPDATE media_file SET play_count = play_count + 1, "
                          "last_played = ?, stats_modified = ? WHERE id = ?");
                q.addBindValue(last);
                q.addBindValue(stamp);
                q.addBindValue(id);
                ok = ok && q.exec();
                if (ok) ok = db.commit();
                if (!ok) {
                    problem =
                        "Cannot record the play: " +
                        (q.lastError().text().isEmpty() ? db.lastError().text()
                                                        : q.lastError().text());
                    db.rollback();
                }
            }
            db.close();
        }
    }
    QSqlDatabase::removeDatabase(name);
    qInfo("database write (play count) took %lld ms", waiting.elapsed());
    if (!problem.isEmpty()) {
        emit message(problem);
        QMetaObject::invokeMethod(
            this,
            [this, id] {
                if (m_unmarkedId == id) m_unmarkedId = -1;
            },
            Qt::QueuedConnection);
        return;
    }
    ++dbGeneration();
    emit playCounted();
}

// The rating of a track (library data, kept in tags.db beside the play count):
// read when it starts, written when the user taps the stars.  The write is
// stamped like a play, so a sync keeps the newer of the phone's and the
// desktop's rating.
void Engine::readRating(qint64 id) {
    if (m_dbPath.isEmpty()) return;
    int rating = -1;
    {
        const QString name =
            "rating-" + QUuid::createUuid().toString(QUuid::Id128);
        {
            QSqlDatabase db = QSqlDatabase::addDatabase("QSQLITE", name);
            db.setDatabaseName(m_dbPath);
            db.setConnectOptions(
                "QSQLITE_OPEN_READONLY;QSQLITE_BUSY_TIMEOUT=3000");
            if (db.open()) {
                QSqlQuery q(db);
                q.prepare("SELECT rating FROM media_file WHERE id = ?");
                q.addBindValue(id);
                if (q.exec() && q.next() && !q.value(0).isNull())
                    rating = q.value(0).toInt();
                db.close();
            }
        }
        QSqlDatabase::removeDatabase(name);
    }
    if (id == m_currentId) m_rating = rating;
    emit ratingKnown(id, rating);
}

void Engine::setRating(qint64 id, int rating) {
    if (m_dbPath.isEmpty() || id < 0) return;
    if (id == m_currentId) {
        m_rating = rating;
        pushState();  // the notification shows the stars
    }
    emit ratingKnown(id, rating);
    m_writes.start([this, path = m_dbPath, id, rating] {
        writeRating(path, id, rating);
    });
}

void Engine::writeRating(const QString &path, qint64 id, int rating) {
    QElapsedTimer waiting;
    waiting.start();
    QMutexLocker lock(&dbLock());
    noteWait(waiting, "rating written");
    const QString name = "rate-" + QUuid::createUuid().toString(QUuid::Id128);
    QString problem;
    {
        QSqlDatabase db = QSqlDatabase::addDatabase("QSQLITE", name);
        db.setDatabaseName(path);
        db.setConnectOptions("QSQLITE_BUSY_TIMEOUT=3000");
        if (!db.open()) {
            problem = db.lastError().text();
        } else {
            QSqlQuery q(db);
            q.exec("PRAGMA table_info(media_file)");
            bool stamped = false;
            while (q.next())
                if (q.value(1).toString() == "stats_modified") stamped = true;
            if (!stamped) {
                problem = "The database is from an older version: sync with "
                          "the desktop to rate tracks.";
            } else {
                q.exec("PRAGMA journal_mode = DELETE");
                q.exec("PRAGMA synchronous = FULL");
                q.prepare("UPDATE media_file SET rating = ?, stats_modified = "
                          "? WHERE id = ?");
                q.addBindValue(rating < 0 ? QVariant() : QVariant(rating));
                q.addBindValue(QDateTime::currentDateTimeUtc().toString(
                    "yyyy-MM-ddTHH:mm:ss.zzzZ"));
                q.addBindValue(id);
                if (!q.exec())
                    problem = "Cannot save the rating: " + q.lastError().text();
            }
            db.close();
        }
    }
    QSqlDatabase::removeDatabase(name);
    qInfo("database write (rating) took %lld ms", waiting.elapsed());
    if (!problem.isEmpty()) {
        emit message(problem);
        QMetaObject::invokeMethod(this, [this, id] { readRating(id); },
                                  Qt::QueuedConnection);  // show what is stored
        return;
    }
    ++dbGeneration();
}

// The counted track has ended or been left: mark it as played in every dynamic
// playlist that holds it (the desktop removes it and adds a replacement at the
// next sync) and take it off the lists on the screen.
void Engine::finishTrack() {
    if (m_unmarkedId < 0) return;
    const qint64 id = m_unmarkedId;
    m_unmarkedId = -1;
    markPlayed({id});
}

// Songs that were skipped (left before they counted as played) go off the
// dynamic playlists only once the listener has settled on a song, i.e. when a
// later one counts as played; going back to a skipped song before that cancels
// the skip.
void Engine::commitSkips() {
    if (m_skipped.isEmpty() || m_unmarkedId != m_currentId)
        return;  // the song that counted must have been written
    const QVector<qint64> ids = m_skipped;
    m_skipped.clear();
    markPlayed(ids);
}

void Engine::markPlayed(const QVector<qint64> &ids) {
    if (m_dbPath.isEmpty()) return;
    m_writes.start([this, path = m_dbPath, ids] { writePlayed(path, ids); });
}

void Engine::writePlayed(const QString &path, const QVector<qint64> &ids) {
    QElapsedTimer waiting;
    waiting.start();
    QMutexLocker lock(&dbLock());
    noteWait(waiting, "played");
    const QString name = "mark-" + QUuid::createUuid().toString(QUuid::Id128);
    QString problem;
    {
        QSqlDatabase db = QSqlDatabase::addDatabase("QSQLITE", name);
        db.setDatabaseName(path);
        db.setConnectOptions("QSQLITE_BUSY_TIMEOUT=3000");
        if (!db.open()) {
            problem = db.lastError().text();
        } else {
            QSqlQuery q(db);
            q.exec("PRAGMA journal_mode = DELETE");
            q.exec("PRAGMA synchronous = FULL");
            const QString stamp = QDateTime::currentDateTimeUtc().toString(
                "yyyy-MM-ddTHH:mm:ss.zzzZ");
            db.transaction();
            for (qint64 id : ids) {
                q.prepare(
                    "UPDATE playlist_item SET removed_at = ? WHERE file_id = ? "
                    "AND removed_at IS NULL AND playlist_id IN "
                    "(SELECT id FROM playlist WHERE rules IS NOT NULL)");
                q.addBindValue(stamp);
                q.addBindValue(id);
                if (!q.exec()) {
                    problem = "Cannot mark the track as played: " +
                              q.lastError().text();
                    break;
                }
            }
            if (problem.isEmpty() && !db.commit())
                problem =
                    "Cannot mark the track as played: " + db.lastError().text();
            if (!problem.isEmpty()) db.rollback();
            db.close();
        }
    }
    QSqlDatabase::removeDatabase(name);
    qInfo("database write (played) took %lld ms", waiting.elapsed());
    if (!problem.isEmpty()) {
        emit message(problem);
        return;
    }
    ++dbGeneration();
    emit playCounted();
    lock.unlock();  // the top-up takes the lock itself
    QMetaObject::invokeMethod(this, [this] { refillLists(); },
                              Qt::QueuedConnection);
}

void Engine::setDatabase(const QString &path) {
    m_dbPath = path;
    QTimer::singleShot(4000, this, [this] { refillLists(); });
}

// Dynamic playlists that have fewer entries than their length (a played track
// has just left) are topped up from their rules, in a thread of their own:
// choosing among thousands of tracks takes a moment.  The desktop's refill
// replaces these entries at the next sync.
void Engine::refillLists() {
    if (m_dbPath.isEmpty()) return;
    QThreadPool::globalInstance()->start([this, path = m_dbPath] {
        QString problem;
        const int changed = topUpDynamicPlaylists(path, &problem);
        if (!problem.isEmpty())
            emit message(problem);
        else if (changed)
            emit playCounted();  // the lists on the screen are read again
        QMetaObject::invokeMethod(this, [this] { continueFromList(); },
                                  Qt::QueuedConnection);
    });
}
