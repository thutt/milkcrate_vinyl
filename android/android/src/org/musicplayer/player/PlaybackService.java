// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
package org.musicplayer.player;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ServiceInfo;
import android.graphics.drawable.Icon;
import android.media.AudioDeviceCallback;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.media.MediaMetadata;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.util.Log;

/**
 * Keeps the music playing in the background: a foreground service with a media
 * notification, lock-screen controls and headset / Bluetooth buttons.  The
 * audio itself is played by the C++ side (QMediaPlayer); this only shows the
 * state and sends the button presses back through command().
 */
public class PlaybackService extends Service {
    public static final int PLAY = 0, PAUSE = 1, NEXT = 2, PREVIOUS = 3,
                            STOP = 4, SEEK = 5, STAR_DOWN = 6, STAR_UP = 7;
    private static final String CUSTOM_STAR_DOWN = "star_down", CUSTOM_STAR_UP =
                                                                    "star_up";
    private static final String CHANNEL = "playback";
    private static final int NOTIFICATION_ID = 1;
    private static final String ACTION_UPDATE = "org.musicplayer.player.UPDATE";
    private static final String ACTION_COMMAND =
        "org.musicplayer.player.COMMAND";
    private static final String ACTION_STOP =
        "org.musicplayer.player.STOPSERVICE";

    /** Implemented in C++ (player.cpp): a button was pressed. */
    public static native void command(int what, long argument);
    // one line for milkcrate-log.txt
    public static native void log(String line);

    private static int R_STAR_DOWN,
        R_STAR_UP;  // drawable ids, looked up by name (this file is built
                    // without the generated R)
    private MediaSession session;
    private boolean foreground = false;
    private AudioManager audioManager;

    // Headphones unplugged or turned off: Android announces it here.
    private final BroadcastReceiver noisy = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            pauseForOutput("becoming noisy");
        }
    };

    // A Bluetooth, wired, USB or car output went away.  The built-in outputs
    // are ignored: they come and go with calls and the like.
    private final AudioDeviceCallback outputs = new AudioDeviceCallback() {
        @Override
        public void onAudioDevicesAdded(AudioDeviceInfo[] added) {
            for (AudioDeviceInfo device : added)
                if (device.isSink())
                    note("output added: " + describe(device));
            report("output added");
        }

        @Override
        public void onAudioDevicesRemoved(AudioDeviceInfo[] removed) {
            for (AudioDeviceInfo device : removed)
                if (device.isSink())
                    note("output removed: " + describe(device));
            for (AudioDeviceInfo device : removed) {
                if (!device.isSink() || !external(device.getType())) continue;
                pauseForOutput("output removed, type " + device.getType());
                return;
            }
        }
    };

    // The music volume changed (the phone's buttons, the head unit, or the
    // system when a Bluetooth device connects).
    private final BroadcastReceiver volume = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            int stream = intent.getIntExtra(
                "android.media.EXTRA_VOLUME_STREAM_TYPE", -1);
            if (stream != AudioManager.STREAM_MUSIC) return;
            report("music volume changed to " + intent.getIntExtra(
                "android.media.EXTRA_VOLUME_STREAM_VALUE", -1));
        }
    };

    private static void note(String line) {
        Log.i("MilkcrateVinyl", line);
        try {
            log(line);
        } catch (UnsatisfiedLinkError e) { /* the C++ side is not loaded yet */
        }
    }

    private static String describe(AudioDeviceInfo device) {
        return "type " + device.getType() + " \"" + device.getProductName()
            + "\"";
    }

    // The music volume the system holds and the outputs it can use, to find
    // out why the sound is sometimes very quiet over Bluetooth.  The dB value
    // is what the system maps the current volume step to for that output.
    public static void report(Context context, String why) {
        report(context.getSystemService(AudioManager.class), why);
    }

    private void report(String why) {
        report(audioManager, why);
    }

    private static void report(AudioManager audio, String why) {
        StringBuilder text = new StringBuilder("audio (" + why + "): music ");
        int step = audio.getStreamVolume(AudioManager.STREAM_MUSIC);
        text.append(step).append(" of ")
            .append(audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC));
        if (audio.isStreamMute(AudioManager.STREAM_MUSIC))
            text.append(" (muted)");
        text.append(", mode ").append(audio.getMode());
        for (AudioDeviceInfo device :
             audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)) {
            if (!external(device.getType())) continue;
            text.append("; ").append(describe(device));
            try {
                text.append(String.format(
                    " %.1f dB", audio.getStreamVolumeDb(
                        AudioManager.STREAM_MUSIC, step, device.getType())));
            } catch (RuntimeException e) { /* not known for this output */
            }
        }
        note(text.toString());
    }

    private static boolean external(int type) {
        switch (type) {
            case AudioDeviceInfo.TYPE_BLUETOOTH_A2DP:
            case AudioDeviceInfo.TYPE_BLUETOOTH_SCO:
            case AudioDeviceInfo.TYPE_WIRED_HEADPHONES:
            case AudioDeviceInfo.TYPE_WIRED_HEADSET:
            case AudioDeviceInfo.TYPE_USB_DEVICE:
            case AudioDeviceInfo.TYPE_USB_HEADSET:
            case AudioDeviceInfo.TYPE_USB_ACCESSORY:
            case AudioDeviceInfo.TYPE_HEARING_AID:
            case AudioDeviceInfo.TYPE_BLE_HEADSET:
            case AudioDeviceInfo.TYPE_BLE_SPEAKER:
                return true;
            default:
                return false;
        }
    }

    private void pauseForOutput(String why) {
        Log.i("MilkcrateVinyl", "pausing: " + why);
        send(PAUSE, 0);
    }

    /** Called from C++ whenever the track or the play state changes. */
    // phase: what the title line shows, for head units that have one line for
    // it: 0 the song's title, 1 the stars and the time left (swapped every 10 s)
    public static void update(Context context, String title, String artist,
                              String album, boolean playing, long position,
                              long duration, int rating, int phase) {
        Intent intent = new Intent(context, PlaybackService.class)
                            .setAction(ACTION_UPDATE)
                            .putExtra("title", title)
                            .putExtra("artist", artist)
                            .putExtra("album", album)
                            .putExtra("phase", phase)
                            .putExtra("playing", playing)
                            .putExtra("position", position)
                            .putExtra("duration", duration)
                            .putExtra("rating", rating);
        context.startForegroundService(intent);
    }

    public static void stop(Context context) {
        context.startService(
            new Intent(context, PlaybackService.class).setAction(ACTION_STOP));
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationChannel channel = new NotificationChannel(
            CHANNEL, "Playback", NotificationManager.IMPORTANCE_LOW);
        channel.setShowBadge(false);
        getSystemService(NotificationManager.class)
            .createNotificationChannel(channel);
        R_STAR_DOWN = getResources().getIdentifier("ic_star_down", "drawable",
                                                   getPackageName());
        R_STAR_UP = getResources().getIdentifier("ic_star_up", "drawable",
                                                 getPackageName());
        session = new MediaSession(this, "Music");
        session.setCallback(new MediaSession.Callback() {
            @Override
            public void onPlay() {
                send(PLAY, 0);
            }
            @Override
            public void onPause() {
                send(PAUSE, 0);
            }
            @Override
            public void onSkipToNext() {
                send(NEXT, 0);
            }
            @Override
            public void onSkipToPrevious() {
                send(PREVIOUS, 0);
            }
            @Override
            public void onStop() {
                send(STOP, 0);
            }
            @Override
            public void onSeekTo(long position) {
                send(SEEK, position);
            }
            @Override
            public void onCustomAction(
                String action,
                android.os.Bundle extras) {  // the lock screen shows these
                                             // beside the transport buttons
                if (CUSTOM_STAR_DOWN.equals(action))
                    send(STAR_DOWN, 0);
                else if (CUSTOM_STAR_UP.equals(action))
                    send(STAR_UP, 0);
            }
        });
        session.setActive(true);
        audioManager = getSystemService(AudioManager.class);
        audioManager.registerAudioDeviceCallback(outputs, new Handler());
        registerReceiver(
            noisy,
            new IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY));
        try {
            registerReceiver(volume, new IntentFilter(
                "android.media.VOLUME_CHANGED_ACTION"));
        } catch (RuntimeException e) {
            Log.w("MilkcrateVinyl", "volume changes not logged: " + e);
        }
        report("service started");
    }

    private static void send(int what, long argument) {
        try {
            command(what, argument);
        } catch (UnsatisfiedLinkError e) { /* the C++ side is not loaded yet */
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        if (ACTION_COMMAND.equals(action)) {
            send(intent.getIntExtra("what", PAUSE), 0);
        } else if (ACTION_UPDATE.equals(action)) {
            show(intent.getStringExtra("title"),
                 intent.getStringExtra("artist"),
                 intent.getStringExtra("album"),
                 intent.getIntExtra("phase", 0),
                 intent.getBooleanExtra("playing", false),
                 intent.getLongExtra("position", 0),
                 intent.getLongExtra("duration", 0),
                 intent.getIntExtra("rating", -1));
        } else if (ACTION_STOP.equals(action) || intent == null) {
            stopForeground(STOP_FOREGROUND_REMOVE);
            foreground = false;
            stopSelf();
        }
        return START_NOT_STICKY;
    }

    private PendingIntent button(int what) {
        Intent intent = new Intent(this, PlaybackService.class)
                            .setAction(ACTION_COMMAND)
                            .putExtra("what", what);
        return PendingIntent.getService(this, what, intent,
                                        PendingIntent.FLAG_IMMUTABLE |
                                            PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private Notification.Action action(int icon, String label, int what) {
        return new Notification.Action
            .Builder(Icon.createWithResource(this, icon), label, button(what))
            .build();
    }

    private static String stars(int rating) {
        if (rating < 0) return "unrated";
        return rating + " \u2605";
    }

    // "-3:42": the time left, as text (the progress bar moves by itself between
    // updates; this changes only when the app sends one)
    private static String left(long position, long duration) {
        if (duration <= 0 || position < 0 || position > duration) return "";
        long seconds = (duration - position + 500) / 1000;
        long hours = seconds / 3600, minutes = seconds / 60 % 60;
        return "-" +
            (hours > 0 ? String.format("%d:%02d:%02d", hours, minutes,
                                       seconds % 60)
                       : String.format("%d:%02d", minutes, seconds % 60));
    }

    private void show(String title, String artist, String album, int phase,
                      boolean playing, long position, long duration,
                      int rating) {
        String time = left(position, duration);
        String info =
            stars(rating) + (time.isEmpty() ? "" : "  \u00b7  " + time);
        String line = artist + "  \u00b7  " + info;  // the notification
        // A head unit may show one line for the title: it alternates between
        // the song's title and the stars and time left.  Artist and album stay.
        session.setMetadata(
            new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE,
                           phase == 1 ? info : title)
                .putString(MediaMetadata.METADATA_KEY_ARTIST, artist)
                .putString(MediaMetadata.METADATA_KEY_ALBUM, album)
                .putLong(MediaMetadata.METADATA_KEY_DURATION, duration)
                .build());
        session.setPlaybackState(
            new PlaybackState.Builder()
                .setActions(
                    PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE |
                    PlaybackState.ACTION_PLAY_PAUSE |
                    PlaybackState.ACTION_SKIP_TO_NEXT |
                    PlaybackState.ACTION_SKIP_TO_PREVIOUS |
                    PlaybackState.ACTION_SEEK_TO | PlaybackState.ACTION_STOP)
                .addCustomAction(CUSTOM_STAR_DOWN,
                                 "One star less (" + stars(rating) + ")",
                                 R_STAR_DOWN)
                .addCustomAction(CUSTOM_STAR_UP,
                                 "One star more (" + stars(rating) + ")",
                                 R_STAR_UP)
                .setState(playing ? PlaybackState.STATE_PLAYING
                                  : PlaybackState.STATE_PAUSED,
                          position, playing ? 1f : 0f)
                .build());
        Intent open = new Intent(this, MusicActivity.class)
                          .setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP |
                                    Intent.FLAG_ACTIVITY_NEW_TASK);
        Notification notification =
            new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(title)
                .setContentText(line)
                .setContentIntent(PendingIntent.getActivity(
                    this, 0, open, PendingIntent.FLAG_IMMUTABLE))
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(playing)
                .setOnlyAlertOnce(true)
                .addAction(action(android.R.drawable.ic_media_previous,
                                  "Previous", PREVIOUS))
                .addAction(playing ? action(android.R.drawable.ic_media_pause,
                                            "Pause", PAUSE)
                                   : action(android.R.drawable.ic_media_play,
                                            "Play", PLAY))
                .addAction(
                    action(android.R.drawable.ic_media_next, "Next", NEXT))
                .addAction(action(R_STAR_DOWN, "One star less", STAR_DOWN))
                .addAction(action(R_STAR_UP, "One star more", STAR_UP))
                .setStyle(new Notification.MediaStyle()
                              .setMediaSession(session.getSessionToken())
                              .setShowActionsInCompactView(0, 1, 2))
                .build();
        if (Build.VERSION.SDK_INT >= 29)
            startForeground(NOTIFICATION_ID, notification,
                            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        else
            startForeground(NOTIFICATION_ID, notification);
        foreground = true;
    }

    /**
     * The app was swiped away from the recent apps.  The Qt user interface
     * cannot be started again inside a process that has lost its activity (the
     * icon would show a blank screen while the song kept playing), so the whole
     * app quits.
     */
    @Override
    public void onTaskRemoved(Intent rootIntent) {
        stopForeground(STOP_FOREGROUND_REMOVE);
        foreground = false;
        stopSelf();
        android.os.Process.killProcess(android.os.Process.myPid());
    }

    @Override
    public void onDestroy() {
        audioManager.unregisterAudioDeviceCallback(outputs);
        unregisterReceiver(noisy);
        try {
            unregisterReceiver(volume);
        } catch (RuntimeException e) { /* it was not registered */
        }
        if (session != null) {
            session.setActive(false);
            session.release();
        }
        super.onDestroy();
    }
}
