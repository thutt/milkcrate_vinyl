// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
package org.musicplayer.player;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaMetadataRetriever;
import java.io.File;
import java.util.Locale;

// The picture of a song: an image in its folder, or one inside the file.
final class CoverArt {
    private CoverArt() {}

    // where the last picture came from, for the log
    static volatile String source = "";

    private static boolean image(String name) {
        String lower = name.toLowerCase(Locale.ROOT);
        return lower.endsWith(".jpg") || lower.endsWith(".jpeg") ||
            lower.endsWith(".png");
    }

    // names that mean "the cover", best first; any other image is the last
    // resort
    private static int rank(String name) {
        String lower = name.toLowerCase(Locale.ROOT);
        String[] best = {"cover", "folder", "front", "album"};
        for (int i = 0; i < best.length; ++i)
            if (lower.startsWith(best[i])) return i;
        return best.length;
    }

    private static Bitmap decode(byte[] data, String file, int side) {
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inJustDecodeBounds = true;
        if (file != null) BitmapFactory.decodeFile(file, options);
        else BitmapFactory.decodeByteArray(data, 0, data.length, options);
        int sample = 1;
        while (options.outWidth / (sample * 2) >= side &&
               options.outHeight / (sample * 2) >= side)
            sample *= 2;
        options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        return file != null
            ? BitmapFactory.decodeFile(file, options)
            : BitmapFactory.decodeByteArray(data, 0, data.length, options);
    }

    // a picture at least side pixels wide when the image is that large, or
    // null when the song has none
    static Bitmap load(String audioPath, int side) {
        source = "none";
        if (audioPath == null || audioPath.isEmpty()) return null;
        try {
            File[] files = new File(audioPath).getParentFile().listFiles();
            String chosen = null;
            if (files != null)
                for (File f : files)
                    if (image(f.getName()) &&
                        (chosen == null ||
                         rank(f.getName()) < rank(new File(chosen).getName())))
                        chosen = f.getPath();
            if (chosen != null) {
                Bitmap bitmap = decode(null, chosen, side);
                if (bitmap != null) {
                    source = "the file " + chosen;
                    return bitmap;
                }
            }
            MediaMetadataRetriever retriever = new MediaMetadataRetriever();
            try {
                retriever.setDataSource(audioPath);
                byte[] data = retriever.getEmbeddedPicture();
                source = data == null ? "none" : "inside the song's file";
                return data == null ? null : decode(data, null, side);
            } finally {
                retriever.release();
            }
        } catch (Exception e) {
            return null;
        }
    }
}
