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
 * The song's ANNOUNCELANGUAGE tag, when it has one, chooses the language of
 * the voice: kanji alone do not say whether a title is Japanese or Chinese.
 */
public final class Announcer {
    private static final String TAG = "MilkcrateVinyl";
    private static final Handler main = new Handler(Looper.getMainLooper());
    private static TextToSpeech speech;
    private static boolean ready = false, failed = false;
    private static String waitingText;  // asked for before the voice was ready
    private static String waitingLanguage;
    private static long waitingId;
    private static Locale spoken;  // the language the voice is set to

    /** The voice for id has ended (the engine ignores ids that are old). */
    public static native void finished(long id);

    public static void say(Context context, String text, String language,
                           long id) {
        final Context app = context.getApplicationContext();
        main.post(() -> sayNow(app, text, language, id));
    }

    // "Japanese", "ja" or "ja-JP" (capitals ignored); null for a name that is
    // not a language, and for an empty one
    static Locale locale(String name) {
        String text = name == null ? "" : name.trim();
        if (text.isEmpty()) return null;
        if (text.length() <= 3 || text.contains("-") || text.contains("_")) {
            Locale tag = Locale.forLanguageTag(text.replace('_', '-'));
            return tag.getLanguage().isEmpty() ? null : tag;
        }
        for (String code : Locale.getISOLanguages()) {
            Locale candidate = new Locale(code);
            if (candidate.getDisplayLanguage(Locale.ENGLISH)
                    .equalsIgnoreCase(text))
                return candidate;
        }
        return null;
    }

    private static void sayNow(Context context, String text, String language,
                               long id) {
        if (failed) {
            finished(id);
            return;
        }
        if (speech == null) {
            waitingText = text;
            waitingLanguage = language;
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
                if (waitingText != null)
                    speak(waitingText, waitingLanguage, waitingId);
                waitingText = null;
            }));
            return;
        }
        if (!ready) {  // still starting: only the latest text is said
            if (waitingText != null) finished(waitingId);
            waitingText = text;
            waitingLanguage = language;
            waitingId = id;
            return;
        }
        speak(text, language, id);
    }

    // The language is set for each text, as the next song may have another.
    // A language that the voice does not have is replaced by the phone's own.
    private static void choose(String language) {
        Locale wanted = locale(language);
        if (wanted != null) {
            int result = speech.setLanguage(wanted);
            if (result >= TextToSpeech.LANG_AVAILABLE) {
                spoken = wanted;
                return;
            }
            Log.w(TAG, "text-to-speech has no voice for " + language);
        } else if (language != null && !language.trim().isEmpty()) {
            Log.w(TAG, "ANNOUNCELANGUAGE is not a language: " + language);
        }
        Locale phone = Locale.getDefault();
        if (!phone.equals(spoken)) {
            speech.setLanguage(phone);
            spoken = phone;
        }
    }

    private static void speak(String text, String language, long id) {
        choose(language);
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
