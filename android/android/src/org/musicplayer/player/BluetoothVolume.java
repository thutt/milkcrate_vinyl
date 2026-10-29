// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
package org.musicplayer.player;

import android.content.Context;
import android.content.SharedPreferences;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;

// The music volume that the user asked to keep for a Bluetooth output.  It is
// kept per device address in the app's own settings (the database on the
// phone is replaced by the desktop's at each sync) and set once when the
// device becomes an output; later changes are left alone.
public final class BluetoothVolume {
    private static final String FILE = "bluetooth_volume";

    private BluetoothVolume() {}

    private static SharedPreferences store(Context context) {
        return context.getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    private static boolean bluetooth(AudioDeviceInfo device) {
        return device.isSink() &&
            (device.getType() == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
             device.getType() == AudioDeviceInfo.TYPE_BLE_HEADSET ||
             device.getType() == AudioDeviceInfo.TYPE_BLE_SPEAKER);
    }

    private static AudioDeviceInfo output(Context context) {
        AudioManager audio = context.getSystemService(AudioManager.class);
        for (AudioDeviceInfo device :
             audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS))
            if (bluetooth(device) && !device.getAddress().isEmpty())
                return device;
        return null;
    }

    // "address<TAB>name" of the Bluetooth output now in use, or ""; the name
    // is the one Android gives to apps (the device's own)
    public static String device(Context context) {
        AudioDeviceInfo device = output(context);
        return device == null
            ? ""
            : device.getAddress() + "\t" + device.getProductName();
    }

    // the saved volume step of an address, or -1
    public static int saved(Context context, String address) {
        return store(context).getInt(address, -1);
    }

    // keeps the music volume now set for the Bluetooth output in use; the
    // step it keeps, or -1 when there is no such output
    public static int save(Context context) {
        AudioDeviceInfo device = output(context);
        if (device == null) return -1;
        int step = context.getSystemService(AudioManager.class)
                       .getStreamVolume(AudioManager.STREAM_MUSIC);
        store(context).edit().putInt(device.getAddress(), step).apply();
        return step;
    }

    public static void forget(Context context, String address) {
        store(context).edit().remove(address).apply();
    }

    // the music volume step now set, and the greatest one
    public static int current(Context context) {
        return context.getSystemService(AudioManager.class)
            .getStreamVolume(AudioManager.STREAM_MUSIC);
    }

    public static int maximum(Context context) {
        return context.getSystemService(AudioManager.class)
            .getStreamMaxVolume(AudioManager.STREAM_MUSIC);
    }

    // called when an output appears: a saved volume is set once
    static void connected(Context context, AudioDeviceInfo device) {
        if (!bluetooth(device)) return;
        int step = saved(context, device.getAddress());
        if (step < 0) return;
        AudioManager audio = context.getSystemService(AudioManager.class);
        audio.setStreamVolume(AudioManager.STREAM_MUSIC,
                              Math.min(step, audio.getStreamMaxVolume(
                                                 AudioManager.STREAM_MUSIC)),
                              0);
    }
}
