// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
package org.musicplayer.player;

import android.content.Intent;
import android.os.Bundle;

import org.qtproject.qt.android.bindings.QtActivity;

/**
 * The app's activity.  When it is closed for good (swiped away from the recent
 * apps) the whole app quits at once: Qt's own shutdown can hang while the music
 * service keeps the process alive (the song went on playing, the icon opened
 * nothing and Android finally reported the app as not responding).
 */
public class MusicActivity extends QtActivity {
    private static boolean started = false;

    /**
     * Android can destroy the activity while the music service keeps the
     * process alive.  Qt cannot build its user interface a second time in the
     * same process (the icon would show nothing, forever), so a second start
     * ends the process and Android starts the app again in a new one.
     */
    @Override
    public void onCreate(Bundle state) {
        if (started) {
            stopService(new Intent(this, PlaybackService.class));
            android.os.Process.killProcess(android.os.Process.myPid());
        }
        started = true;
        super.onCreate(state);
    }

    @Override
    protected void onDestroy() {
        if (isFinishing()) {
            stopService(new Intent(this, PlaybackService.class));
            android.os.Process.killProcess(android.os.Process.myPid());
        }
        super.onDestroy();
    }
}
