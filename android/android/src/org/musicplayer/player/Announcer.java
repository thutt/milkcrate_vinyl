// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
package org.musicplayer.player;

import android.content.Context;
import android.media.AudioAttributes;
import android.os.Handler;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;
import java.util.Locale;

/**
 * Says the name of a song with the phone's own text-to-speech.  The engine
 * (C++) asks for a text with an identifier and is told through finished(id)
 * when the voice has ended, was cut off by a later text, or cannot be used.
 */
public final class Announcer {
    private static final String TAG = "MilkcrateVinyl";
    private static final Handler main = new Handler(Looper.getMainLooper());
    private static TextToSpeech speech;
    private static boolean ready = false, failed = false;
    private static String waitingText;  // asked for before the voice was ready
    private static long waitingId;

    /** The voice for id has ended (the engine ignores ids that are old). */
    public static native void finished(long id);

    public static void say(Context context, String text, long id) {
        final Context app = context.getApplicationContext();
        main.post(() -> sayNow(app, text, id));
    }

    private static void sayNow(Context context, String text, long id) {
        if (failed) {
            finished(id);
            return;
        }
        if (speech == null) {
            waitingText = text;
            waitingId = id;
            speech = new TextToSpeech(context, status -> main.post(() -> {
                if (status != TextToSpeech.SUCCESS) {
                    Log.w(TAG, "text-to-speech cannot start: " + status);
                    failed = true;
                    if (waitingText != null) finished(waitingId);
                    waitingText = null;
                    return;
                }
                speech.setAudioAttributes(
                    new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build());
                speech.setLanguage(Locale.getDefault());
                speech.setOnUtteranceProgressListener(
                    new UtteranceProgressListener() {
                        @Override
                        public void onStart(String utterance) {}

                        @Override
                        public void onDone(String utterance) {
                            ended(utterance);
                        }

                        @Override
                        public void onStop(String utterance, boolean done) {
                            ended(utterance);
                        }

                        @Override
                        public void onError(String utterance) {
                            ended(utterance);
                        }
                    });
                ready = true;
                if (waitingText != null) speak(waitingText, waitingId);
                waitingText = null;
            }));
            return;
        }
        if (!ready) {  // still starting: only the latest text is said
            if (waitingText != null) finished(waitingId);
            waitingText = text;
            waitingId = id;
            return;
        }
        speak(text, id);
    }

    private static void speak(String text, long id) {
        // QUEUE_FLUSH: a text for a later song replaces the one being said
        if (speech.speak(text, TextToSpeech.QUEUE_FLUSH, null,
                         Long.toString(id)) != TextToSpeech.SUCCESS) {
            Log.w(TAG, "text-to-speech refused: " + text);
            finished(id);
        }
    }

    private static void ended(String utterance) {
        try {
            finished(Long.parseLong(utterance));
        } catch (NumberFormatException e) {
            Log.w(TAG, "text-to-speech ended with " + utterance);
        }
    }
}
