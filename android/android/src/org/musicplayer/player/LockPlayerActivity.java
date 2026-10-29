// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
package org.musicplayer.player;

import android.app.Activity;
import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;
import android.content.pm.ActivityInfo;
import android.view.GestureDetector;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.OrientationEventListener;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.SeekBar;
import android.widget.TextView;

// The song that is playing, full screen, over the lock screen: it opens when
// the screen is turned on while music plays (see PlaybackService), and goes
// away when the phone is unlocked or the screen turns off.
public class LockPlayerActivity extends Activity {
    // what the service last reported; replaced as a whole
    static final class State {
        String title = "", artist = "", album = "", path = "";
        boolean playing;
        long position, duration;
        int rating = -1;
        long at = SystemClock.elapsedRealtime();
        Bitmap cover;
    }

    static volatile State state = new State();
    // "EQ off", "EQ: Rock" or "EQ: Jazz (auto)"
    static volatile String equalizer = "";
    // the line the song's row shows: why it was chosen, or its plays
    static volatile String detail = "";
    private static LockPlayerActivity shown;

    /** Implemented in C++ (player.cpp): see EqView.set. */
    static native float[] spectrum();

    /** Implemented in C++ (player.cpp): 0 Automatic on or off, 1 the gain of
     *  a band, 2 the next (1) or previous (-1) preset. */
    static native void adjust(int what, int index, float value);

    /** Implemented in C++ (player.cpp): a line for the log file. */
    static native void log(String line);
    private static final String FILE = "lock_player";

    private final Handler handler = new Handler(Looper.getMainLooper());
    private ImageView cover;
    private TextView title, artist, album, elapsed, remaining, clock, info;
    private SeekBar seek;
    private GlyphButton play;
    private StarView star;
    private EqView eq;
    private Pill bypass, auto;
    private boolean dragging = false, reported = false;
    // a swipe anywhere but on the seek bar closes the view and shows the lock
    // screen again
    private GestureDetector swipe;
    // Android does not turn a phone's screen upside down by itself, so the
    // view asks for it when the phone is held that way (the accelerometer
    // is read whatever the setting of the automatic rotation)
    private OrientationEventListener tilt;
    private boolean upsideDown = false;

    private final BroadcastReceiver away = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            finish();
        }
    };

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            draw();
            handler.postDelayed(this, 500);
        }
    };

    // the five bars move about thirty times a second
    private final Runnable moving = new Runnable() {
        @Override
        public void run() {
            try {
                eq.set(spectrum());
                auto.set(eq.auto ? "Auto EQ: On" : "Auto EQ: Off", eq.auto,
                         eq.live);
                bypass.set(eq.live && !eq.bypassed ? "EQ: On" : "EQ: Off",
                           eq.live && !eq.bypassed, eq.live);
            } catch (UnsatisfiedLinkError e) {
                if (!reported) Log.w("MilkcrateVinyl", "spectrum: " + e);
                reported = true;  // the C++ side is not loaded
            }
            handler.postDelayed(this, 33);
        }
    };

    public static void setDetail(String line) {
        detail = line;
        changed();
    }

    // called from C++ whenever the setting changes
    public static void setEqualizerName(String name) {
        equalizer = name;
        changed();
    }

    static boolean visible() {
        return shown != null;
    }

    static boolean enabled(Context context) {
        return context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getBoolean("on", true);
    }

    // Android 14 and later make the user allow full-screen notifications for
    // an app that is not a phone or an alarm; true when they are allowed
    static boolean allowed(Context context) {
        return Build.VERSION.SDK_INT < 34 ||
            context.getSystemService(NotificationManager.class)
                .canUseFullScreenIntent();
    }

    // switching it on without the permission opens the page of settings
    public static void setEnabled(Context context, boolean on) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .edit().putBoolean("on", on).apply();
        if (on && !allowed(context))
            context.startActivity(
                new Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                           Uri.parse("package:" + context.getPackageName()))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    }

    public static boolean isAllowed(Context context) {
        return allowed(context);
    }

    // the page of settings where full-screen notifications are allowed
    public static void openSettings(Context context) {
        if (Build.VERSION.SDK_INT >= 34)
            context.startActivity(
                new Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                           Uri.parse("package:" + context.getPackageName()))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    }

    public static boolean isEnabled(Context context) {
        return enabled(context);
    }

    static void changed() {
        final LockPlayerActivity activity = shown;
        if (activity != null) activity.handler.post(activity::draw);
    }

    private int dp(float value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private TextView text(float size, boolean bold, int color) {
        TextView view = new TextView(this);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setGravity(Gravity.CENTER);
        view.setSingleLine(true);
        view.setEllipsize(android.text.TextUtils.TruncateAt.END);
        if (bold) view.setTypeface(view.getTypeface(), 1);
        return view;
    }

    // a transport or arrow button, drawn crisp at any size
    private GlyphButton icon(int glyph, String label, final int what) {
        GlyphButton button = new GlyphButton(this, glyph);
        button.setContentDescription(label);
        button.setOnClickListener(v -> PlaybackService.send(what, 0));
        LinearLayout.LayoutParams layout =
            new LinearLayout.LayoutParams(dp(48), dp(48));
        layout.setMargins(dp(8), 0, dp(8), 0);
        button.setLayoutParams(layout);
        return button;
    }

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setShowWhenLocked(true);
        setTurnScreenOn(true);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        int white = Color.WHITE, grey = Color.argb(180, 255, 255, 255);

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Color.BLACK);
        // without a cover the rest sits in the middle of the screen
        page.setGravity(Gravity.CENTER_HORIZONTAL | Gravity.CENTER_VERTICAL);
        page.setClipChildren(false);
        page.setPadding(dp(20), dp(24), dp(20), dp(16));

        // the program's name on two lines beside its icon, at the upper left
        LinearLayout brand = new LinearLayout(this);
        brand.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        ImageView mark = new ImageView(this);
        int icon = getResources().getIdentifier("ic_launcher", "mipmap",
                                                getPackageName());
        if (icon != 0) mark.setImageResource(icon);
        brand.addView(mark, new LinearLayout.LayoutParams(dp(44), dp(44)));
        TextView name = text(13, true, Color.rgb(226, 200, 150));
        name.setText("MILKCRATE\nVINYL");
        name.setSingleLine(false);
        name.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        name.setLetterSpacing(0.2f);
        name.setPadding(dp(10), 0, 0, 0);
        brand.addView(name);
        page.addView(brand, new LinearLayout.LayoutParams(-1, -2));

        clock = text(34, false, white);
        page.addView(clock);

        cover = new ImageView(this);
        cover.setScaleType(ImageView.ScaleType.FIT_CENTER);
        page.addView(cover, new LinearLayout.LayoutParams(
                                -1, 0, 1f));  // takes what the text leaves
        ((LinearLayout.LayoutParams)cover.getLayoutParams())
            .setMargins(0, dp(16), 0, dp(16));

        title = text(26, true, white);
        artist = text(19, false, white);
        album = text(15, false, grey);
        page.addView(title);
        page.addView(artist);
        page.addView(album);
        info = text(13, false, grey);
        info.setSingleLine(false);
        info.setMaxLines(2);
        page.addView(info);

        seek = new SeekBar(this);
        seek.setMax(1000);
        seek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar bar, int value,
                                          boolean user) {}

            @Override
            public void onStartTrackingTouch(SeekBar bar) {
                dragging = true;
            }

            @Override
            public void onStopTrackingTouch(SeekBar bar) {
                dragging = false;
                PlaybackService.send(PlaybackService.SEEK,
                                     state.duration * bar.getProgress() / 1000);
            }
        });
        LinearLayout.LayoutParams seekLayout =
            new LinearLayout.LayoutParams(-1, -2);
        seekLayout.setMargins(0, dp(12), 0, 0);
        page.addView(seek, seekLayout);

        LinearLayout times = new LinearLayout(this);
        elapsed = text(13, false, grey);
        remaining = text(13, false, grey);
        elapsed.setGravity(Gravity.START);
        remaining.setGravity(Gravity.END);
        times.addView(elapsed, new LinearLayout.LayoutParams(0, -2, 1f));
        times.addView(remaining, new LinearLayout.LayoutParams(0, -2, 1f));
        page.addView(times, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout controls = new LinearLayout(this);
        controls.setGravity(Gravity.CENTER);
        controls.addView(icon(GlyphButton.PREVIOUS, "Previous",
                              PlaybackService.PREVIOUS));
        play = icon(GlyphButton.PAUSE, "Play or pause", -1);
        play.setOnClickListener(v -> PlaybackService.send(
            state.playing ? PlaybackService.PAUSE : PlaybackService.PLAY, 0));
        controls.addView(play);
        controls.addView(icon(GlyphButton.NEXT, "Next", PlaybackService.NEXT));
        LinearLayout.LayoutParams controlsLayout =
            new LinearLayout.LayoutParams(-1, -2);
        controlsLayout.setMargins(0, dp(10), 0, 0);
        page.addView(controls, controlsLayout);

        // the five bands of the equalizer: its setting and the sound in each
        eq = new EqView(this);
        LinearLayout.LayoutParams eqLayout =
            new LinearLayout.LayoutParams(-1, dp(108));
        eqLayout.setMargins(0, dp(10), 0, 0);
        page.addView(eq, eqLayout);

        // Automatic on or off, and the equalizer heard or not (a song played
        // flat; the setting is not changed)
        LinearLayout switches = new LinearLayout(this);
        switches.setGravity(Gravity.CENTER);
        // room above and below, so that nothing of a pill is cut off
        switches.setClipChildren(false);
        switches.setPadding(0, dp(6), 0, dp(10));
        auto = new Pill(this);
        auto.setOnClickListener(v -> adjust(0, 0, 0));
        bypass = new Pill(this);
        bypass.setOnClickListener(
            v -> PlaybackService.send(PlaybackService.BYPASS, 0));
        LinearLayout.LayoutParams pill = new LinearLayout.LayoutParams(
            dp(136), dp(42));
        pill.setMargins(dp(8), 0, dp(8), 0);
        switches.addView(auto, pill);
        switches.addView(bypass, pill);
        page.addView(switches, new LinearLayout.LayoutParams(-1, -2));

        // the rating: a star holding the number of stars, with an arrow
        // down and an arrow up on either side
        LinearLayout rate = new LinearLayout(this);
        rate.setGravity(Gravity.CENTER);
        rate.addView(icon(GlyphButton.DOWN, "One star less",
                          PlaybackService.STAR_DOWN));
        star = new StarView(this);
        LinearLayout.LayoutParams starLayout =
            new LinearLayout.LayoutParams(dp(60), dp(60));
        starLayout.setMargins(dp(8), 0, dp(8), 0);
        rate.addView(star, starLayout);
        rate.addView(icon(GlyphButton.UP, "One star more",
                          PlaybackService.STAR_UP));
        LinearLayout.LayoutParams rateLayout =
            new LinearLayout.LayoutParams(-1, -2);
        rateLayout.setMargins(0, dp(6), 0, 0);
        page.addView(rate, rateLayout);

        swipe = new GestureDetector(this,
            new GestureDetector.SimpleOnGestureListener() {
                @Override
                public boolean onFling(MotionEvent from, MotionEvent to,
                                       float vx, float vy) {
                    if (from == null || dragging || eq.touching) return false;
                    float far = dp(80);
                    if (Math.abs(to.getX() - from.getX()) < far &&
                        Math.abs(to.getY() - from.getY()) < far)
                        return false;
                    finish();
                    return true;
                }
            });
        tilt = new OrientationEventListener(this) {
            @Override
            public void onOrientationChanged(int degrees) {
                if (degrees == ORIENTATION_UNKNOWN) return;
                // a margin on either side keeps it from flipping back and
                // forth near the middle
                boolean down = upsideDown ? degrees > 120 && degrees < 240
                                          : degrees > 150 && degrees < 210;
                if (down == upsideDown) return;
                upsideDown = down;
                setRequestedOrientation(
                    down ? ActivityInfo.SCREEN_ORIENTATION_REVERSE_PORTRAIT
                         : ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);
            }
        };
        setContentView(page);
        getWindow().getInsetsController().hide(WindowInsets.Type.systemBars());
        IntentFilter filter = new IntentFilter(Intent.ACTION_USER_PRESENT);
        filter.addAction(Intent.ACTION_SCREEN_OFF);
        registerReceiver(away, filter, Context.RECEIVER_NOT_EXPORTED);
    }

    @Override
    protected void onStart() {
        super.onStart();
        shown = this;
        if (tilt.canDetectOrientation()) tilt.enable();
        handler.post(tick);
        handler.post(moving);
    }

    @Override
    protected void onStop() {
        handler.removeCallbacks(tick);
        tilt.disable();
        handler.removeCallbacks(moving);
        if (shown == this) shown = null;
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        unregisterReceiver(away);
        super.onDestroy();
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent event) {
        swipe.onTouchEvent(event);
        return super.dispatchTouchEvent(event);
    }

    private static String time(long ms) {
        long seconds = Math.max(0, ms / 1000);
        return String.format("%d:%02d", seconds / 60, seconds % 60);
    }

    private void draw() {
        State s = state;
        long position = s.position;
        if (s.playing)
            position += SystemClock.elapsedRealtime() - s.at;
        if (s.duration > 0 && position > s.duration) position = s.duration;
        title.setText(s.title.isEmpty() ? "Nothing playing" : s.title);
        artist.setText(s.artist);
        album.setText(s.album);
        info.setText(detail);
        info.setVisibility(detail.isEmpty() ? View.GONE : View.VISIBLE);
        star.set(s.rating);
        cover.setImageBitmap(s.cover);
        cover.setVisibility(s.cover == null ? View.GONE : View.VISIBLE);
        play.set(s.playing ? GlyphButton.PAUSE : GlyphButton.PLAY);
        elapsed.setText(time(position));
        remaining.setText(s.duration > 0 ? "-" + time(s.duration - position)
                                         : "");
        if (!dragging && s.duration > 0)
            seek.setProgress((int)(position * 1000 / s.duration));
        clock.setText(android.text.format.DateFormat.getTimeFormat(this)
                          .format(new java.util.Date()));
    }

    // A flat button with a picture drawn from paths, so that it is sharp.
    static final class GlyphButton extends View {
        static final int PREVIOUS = 0, PLAY = 1, PAUSE = 2, NEXT = 3, DOWN = 4,
                         UP = 5;
        private int glyph;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Path path = new Path();

        GlyphButton(Context context, int glyph) {
            super(context);
            this.glyph = glyph;
            setClickable(true);
        }

        void set(int glyph) {
            if (glyph == this.glyph) return;
            this.glyph = glyph;
            invalidate();
        }

        private void triangle(float x0, float y0, float x1, float y1, float x2,
                              float y2) {
            path.moveTo(x0, y0);
            path.lineTo(x1, y1);
            path.lineTo(x2, y2);
            path.close();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            float w = getWidth(), h = getHeight();
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(isPressed() ? Color.argb(110, 255, 255, 255)
                                       : Color.argb(55, 255, 255, 255));
            canvas.drawRoundRect(new RectF(0, 0, w, h), w * 0.22f, w * 0.22f,
                                 paint);
            paint.setColor(Color.WHITE);
            float u = w * 0.14f, l = w * 0.3f, r = w * 0.7f, t = h * 0.28f,
                  b = h * 0.72f, m = h * 0.5f;
            path.reset();
            switch (glyph) {
                case PLAY:
                    triangle(l + u * 0.3f, t, l + u * 0.3f, b, r + u * 0.3f, m);
                    break;
                case PAUSE:
                    path.addRect(l + u * 0.2f, t, l + u * 1.7f, b,
                                 Path.Direction.CW);
                    path.addRect(r - u * 1.7f, t, r - u * 0.2f, b,
                                 Path.Direction.CW);
                    break;
                case NEXT:
                    triangle(l, t, l, b, m, m);
                    triangle(m, t, m, b, r, m);
                    path.addRect(r, t, r + u * 0.5f, b, Path.Direction.CW);
                    break;
                case PREVIOUS:
                    triangle(r, t, r, b, m, m);
                    triangle(m, t, m, b, l, m);
                    path.addRect(l - u * 0.5f, t, l, b, Path.Direction.CW);
                    break;
                case DOWN:
                    triangle(l, h * 0.36f, r, h * 0.36f, w * 0.5f, h * 0.68f);
                    break;
                default:
                    triangle(l, h * 0.64f, r, h * 0.64f, w * 0.5f, h * 0.32f);
                    break;
            }
            canvas.drawPath(path, paint);
        }

        @Override
        public void setPressed(boolean pressed) {
            super.setPressed(pressed);
            invalidate();
        }
    }

    // A five-pointed star with the number of stars in its middle.
    static final class StarView extends View {
        private int value = -1;
        private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint number = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Path path = new Path();

        StarView(Context context) {
            super(context);
            fill.setColor(Color.rgb(255, 193, 7));
            number.setColor(Color.BLACK);
            number.setTextAlign(Paint.Align.CENTER);
            number.setFakeBoldText(true);
        }

        void set(int value) {
            if (value == this.value) return;
            this.value = value;
            invalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            float cx = getWidth() / 2f, cy = getHeight() / 2f;
            float outer = Math.min(getWidth(), getHeight()) / 2f,
                  inner = outer * 0.5f;
            path.reset();
            for (int i = 0; i < 10; ++i) {
                double angle = Math.PI * (-0.5 + i / 5.0);
                float radius = i % 2 == 0 ? outer : inner;
                float x = cx + (float)(Math.cos(angle) * radius),
                      y = cy + (float)(Math.sin(angle) * radius);
                if (i == 0) path.moveTo(x, y);
                else path.lineTo(x, y);
            }
            path.close();
            canvas.drawPath(path, fill);
            number.setTextSize(outer * 0.8f);
            String text = value < 0 ? "\u2013" : String.valueOf(value);
            // centred on the middle of the star, not of the line of text
            float y = cy - (number.ascent() + number.descent()) / 2f;
            canvas.drawText(text, cx, y, number);
        }
    }

    // Six vertical bars made of lit segments, like a vacuum fluorescent
    // display: one for each band of the equalizer and, on the right, one for
    // the whole sound.  The dim track is the range of the setting (-12 to
    // +12 dB) and a white line across it is the setting; the lit segments
    // rising from the bottom are how loud the song is in that band.
    static final class EqView extends View {
        private static final String[] NAMES = {"60 Hz", "230 Hz", "910 Hz",
                                               "3.6 kHz", "14 kHz", "all"};
        private static final int SEGMENTS = 16, COLUMNS = 6;
        // an 80s display seen through its blue filter
        private static final int LIT = Color.rgb(0, 210, 255),
                                 TIP = Color.rgb(190, 245, 255),
                                 DIM = Color.argb(34, 0, 210, 255),
                                 RED = Color.rgb(255, 36, 24);  // an LED
        private final float[] gain = new float[5], shown = new float[COLUMNS],
                              peak = new float[COLUMNS];
        boolean live = false, bypassed = false, auto = false, locked = false;
        boolean touching = false;  // a finger is setting a band
        private float trackTop, trackBottom;  // as last drawn
        private int last = -100;  // the last gain sent while dragging
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);

        EqView(Context context) {
            super(context);
        }

        // values: see LockPlayerActivity.spectrum
        void set(float[] values) {
            live = values[0] != 0;
            bypassed = values[12] != 0;
            auto = values[13] != 0;
            locked = values[14] != 0;
            for (int b = 0; b < 5; ++b) gain[b] = values[1 + b];
            for (int b = 0; b < COLUMNS; ++b) {
                float level = live ? values[6 + b] : 0;
                // rises at once, falls at a steady speed
                shown[b] = level > shown[b] ? level
                                            : Math.max(level, shown[b] - 0.04f);
                // the top segment is pushed up at once and sinks slowly
                peak[b] = level > peak[b] ? level
                                          : Math.max(level, peak[b] - 0.006f);
            }
            invalidate();
        }

        // Dragging in a column moves that band's setting, while the equalizer
        // is on and Automatic has not chosen the preset.
        @Override
        public boolean onTouchEvent(MotionEvent event) {
            int action = event.getActionMasked();
            if (action == MotionEvent.ACTION_UP ||
                action == MotionEvent.ACTION_CANCEL) {
                touching = false;
                last = -100;
                return true;
            }
            if (!live || locked || trackBottom <= trackTop) return false;
            if (event.getY() < trackTop) {  // the arrows beside the name
                if (action == MotionEvent.ACTION_DOWN)
                    adjust(2, event.getX() < getWidth() / 2f ? -1 : 1, 0);
                return true;
            }
            touching = true;
            int band = (int)(event.getX() / (getWidth() / (float)COLUMNS));
            if (band < 0 || band > 4) return true;  // the whole sound has none
            float middle = (trackTop + trackBottom) / 2f;
            float gain = (middle - event.getY()) /
                         ((trackBottom - trackTop) / 2f) * 12f;
            int rounded = Math.round(Math.max(-12f, Math.min(12f, gain)));
            int key = band * 100 + rounded + 12;
            if (key != last) {  // sends only a change
                last = key;
                adjust(1, band, rounded);
            }
            return true;
        }

        @Override
        protected void onDraw(Canvas canvas) {
            float w = getWidth(), h = getHeight(),
                  d = getResources().getDisplayMetrics().density;
            float label = 16 * d;
            trackTop = 30 * d;
            trackBottom = h - label;
            float column = w / COLUMNS, bar = Math.min(column * 0.66f, 48 * d);
            float gap = 2 * d;
            float segment = (trackBottom - trackTop - gap * (SEGMENTS - 1)) /
                            SEGMENTS;
            paint.setTextAlign(Paint.Align.CENTER);
            paint.setStyle(Paint.Style.FILL);
            for (int b = 0; b < COLUMNS; ++b) {
                float cx = column * (b + 0.5f);
                int lit = (int)Math.ceil(shown[b] * SEGMENTS - 0.001f);
                int top = (int)Math.ceil(peak[b] * SEGMENTS - 0.001f);
                for (int i = 0; i < SEGMENTS; ++i) {
                    float bottom = trackBottom - i * (segment + gap);
                    boolean on = i < lit;
                    // the bright top segment: the end of the bar while it is
                    // pushed up, and LED red once it sinks slowly above it
                    paint.setColor(i == top - 1 ? (top > lit ? RED : TIP)
                                                : on ? LIT : DIM);
                    canvas.drawRect(cx - bar / 2, bottom - segment,
                                    cx + bar / 2, bottom, paint);
                }
                if (b < 5) {  // the setting of the band
                    float middle = (trackTop + trackBottom) / 2f;
                    float y = middle -
                              gain[b] / 12f * (trackBottom - trackTop) / 2f;
                    // the setting: a line in the muted orange of an 80s
                    // Yamaha or Pioneer equalizer, with a soft glow
                    paint.setColor(Color.argb(60, 222, 132, 62));
                    canvas.drawRect(cx - bar / 2 - 4 * d, y - 4 * d,
                                    cx + bar / 2 + 4 * d, y + 4 * d, paint);
                    paint.setColor(Color.rgb(226, 138, 68));
                    canvas.drawRect(cx - bar / 2 - 3 * d, y - 1.5f * d,
                                    cx + bar / 2 + 3 * d, y + 1.5f * d, paint);
                }
                paint.setColor(Color.argb(210, 226, 200, 150));  // champagne
                paint.setTextSize(11 * d);
                canvas.drawText(NAMES[b], cx, h - 3 * d, paint);
            }
            // the name of the setting with an arrow on either side
            paint.setTextSize(14 * d);
            paint.setColor(live ? Color.rgb(240, 214, 168)
                                : Color.argb(200, 226, 200, 150));
            canvas.drawText(equalizer, w / 2f, 20 * d, paint);
            float half = paint.measureText(equalizer) / 2f + 22 * d;
            paint.setColor(Color.argb(live && !locked ? 255 : 70, 226, 200,
                                      150));
            paint.setTextSize(20 * d);
            canvas.drawText("\u25c0", w / 2f - half, 22 * d, paint);
            canvas.drawText("\u25b6", w / 2f + half, 22 * d, paint);
        }
    }

    // A switch drawn as a pill: lit when on, an outline when off, dim when it
    // cannot be used.
    static final class Pill extends View {
        private String text = "";
        private boolean lit = false;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);

        Pill(Context context) {
            super(context);
            setClickable(true);
        }

        void set(String text, boolean lit, boolean usable) {
            if (text.equals(this.text) && lit == this.lit &&
                usable == isEnabled())
                return;
            this.text = text;
            this.lit = lit;
            setEnabled(usable);
            invalidate();
        }

        @Override
        public void setPressed(boolean pressed) {
            super.setPressed(pressed);
            invalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            float w = getWidth(), h = getHeight(),
                  d = getResources().getDisplayMetrics().density;
            int alpha = isEnabled() ? 255 : 90;
            RectF shape = new RectF(2 * d, 2 * d, w - 2 * d, h - 2 * d);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(lit ? Color.argb(alpha, 0, 210, 255)
                               : Color.argb(isPressed() ? 70 : 25, 255, 255,
                                            255));
            canvas.drawRoundRect(shape, h / 2f, h / 2f, paint);
            if (!lit) {
                paint.setStyle(Paint.Style.STROKE);
                paint.setStrokeWidth(1.5f * d);
                paint.setColor(Color.argb(alpha, 226, 190, 120));
                canvas.drawRoundRect(shape, h / 2f, h / 2f, paint);
            }
            paint.setStyle(Paint.Style.FILL);
            paint.setTextAlign(Paint.Align.CENTER);
            paint.setTextSize(16 * d);
            paint.setFakeBoldText(true);
            paint.setColor(lit ? Color.argb(alpha, 0, 30, 40)
                               : Color.argb(alpha, 255, 255, 255));
            canvas.drawText(text, w / 2f,
                            h / 2f - (paint.ascent() + paint.descent()) / 2f,
                            paint);
        }
    }
}
