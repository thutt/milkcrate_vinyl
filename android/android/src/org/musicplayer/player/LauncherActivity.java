// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
package org.musicplayer.player;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.widget.Toast;

import java.io.File;

/**
 * The activity the launcher icon starts.  When the phone's Android is new
 * enough it starts the player and ends; otherwise it says what the phone has
 * and what the player needs, because the package installer's only complaint
 * about an old phone is a bare "problem parsing the package".  The player's
 * own activity cannot do this: it cannot be started on a phone that is too
 * old without loading the Qt libraries.
 */
public class LauncherActivity extends Activity {
    static final int REQUIRED_API = 33;
    static final String REQUIRED_NAME = "13";
    private static final String[] SUFFIXES = {"", "-journal", "-wal", "-shm"};

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (Build.VERSION.SDK_INT >= REQUIRED_API) {
            Intent play = new Intent(this, MusicActivity.class);
            play.addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                          | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            startActivity(play);
            finish();
            return;
        }
        String text = "This phone has Android " + Build.VERSION.RELEASE
            + " (API level " + Build.VERSION.SDK_INT + ", "
            + Build.MANUFACTURER + " " + Build.MODEL + ").\n\n"
            + "Milkcrate Vinyl needs Android " + REQUIRED_NAME
            + " (API level " + REQUIRED_API + ") or newer, so it cannot run "
            + "here.\n\n"
            + "Uninstall removes the app and the library database "
            + "(tags.db), which holds the play counts and ratings kept on "
            + "this phone.  The music files are not touched.";
        new AlertDialog.Builder(
                this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Android is too old")
            .setMessage(text)
            .setCancelable(false)
            .setPositiveButton("Ok", (d, which) -> finish())
            .setNegativeButton("Uninstall", (d, which) -> uninstall())
            .show();
    }

    private void uninstall() {
        int failed = removeDatabases();
        if (failed > 0)
            Toast.makeText(this, "Could not delete " + failed
                           + " database file(s)", Toast.LENGTH_LONG).show();
        Intent remove = new Intent(Intent.ACTION_DELETE,
                                   Uri.parse("package:" + getPackageName()));
        startActivity(remove);
        finish();
    }

    /**
     * Deletes tags.db (and its journal files) from every place the player
     * keeps it: the MusicLibrary folder of each storage volume and the
     * player's own folders.  Returns how many existing files could not be
     * deleted.
     */
    private int removeDatabases() {
        java.util.ArrayList<File> folders = new java.util.ArrayList<>();
        File[] dirs = getExternalFilesDirs(null);
        for (int i = 1; dirs != null && i < dirs.length; i++) {
            if (dirs[i] == null) continue;
            String path = dirs[i].getAbsolutePath();
            int cut = path.indexOf("/Android/data/");
            if (cut > 0) folders.add(new File(path.substring(0, cut),
                                              "MusicLibrary"));
        }
        folders.add(new File("/storage/emulated/0/MusicLibrary"));
        folders.add(getFilesDir());
        if (dirs != null && dirs.length > 0 && dirs[0] != null)
            folders.add(dirs[0]);
        int failed = 0;
        for (File folder : folders)
            for (String suffix : SUFFIXES) {
                File f = new File(folder, "tags.db" + suffix);
                if (f.exists() && !f.delete()) failed++;
            }
        return failed;
    }
}
