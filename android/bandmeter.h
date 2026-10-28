// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
// Measures how loud the low, middle and high parts of the sound are, for the
// three bars beside the song that is playing.  The sound is split with
// fourth-order filters (low: below 250 Hz; middle: 250 Hz to 4 kHz; high:
// above 4 kHz).  About thirty times a second each part gets a level from 0
// to 1: how far its loudness is below the loudest part has lately been.
// Music has far more energy in the bass than in the treble, so the parts
// are weighted (the middle by 2.5, the high by 8) before they are compared;
// a pure bass note then moves only the low bar, and a typical song moves
// all three.
#pragma once
#include <algorithm>
#include <cmath>

class BandMeter {
public:
    static constexpr int Bands = 3;
    static constexpr double LowEdge = 250, HighEdge = 4000;
    static constexpr double RangeDb = 36;  // below the recent peak: level 0
    static constexpr double StartPeak = 0.1;  // the peak a song starts from
    static constexpr double PeakKeep = 0.998;  // per window; falls slowly
    static constexpr double Weight[Bands] = {1.0, 2.5, 8.0};

    void setRate(int rate) {
        if (rate == m_rate) return;
        m_rate = rate;
        m_window = std::max(1, rate / 30);
        m_low.design(LowEdge, rate, false);
        m_midHigh.design(LowEdge, rate, true);
        m_midLow.design(HighEdge, rate, false);
        m_high.design(HighEdge, rate, true);
        reset();
    }

    void reset() {  // a new song
        m_low.clear();
        m_midHigh.clear();
        m_midLow.clear();
        m_high.clear();
        for (int b = 0; b < Bands; ++b) {
            m_sum[b] = 0;
            m_level[b] = 0;
        }
        m_peak = StartPeak;
        m_count = 0;
        m_fresh = false;
    }

    // One sample of the sound (the two channels added and halved).
    void add(float x) {
        double low = m_low.process(x);
        double mid = m_midLow.process(m_midHigh.process(x));
        double high = m_high.process(x);
        m_sum[0] += low * low;
        m_sum[1] += mid * mid;
        m_sum[2] += high * high;
        if (++m_count < m_window) return;
        double weighted[Bands], loudest = 1e-5;
        for (int b = 0; b < Bands; ++b) {
            weighted[b] = std::sqrt(m_sum[b] / m_count) * Weight[b];
            loudest = std::max(loudest, weighted[b]);
            m_sum[b] = 0;
        }
        m_peak = std::max(m_peak * PeakKeep, loudest);
        for (int b = 0; b < Bands; ++b) {
            double db = 20 * std::log10(std::max(weighted[b], 1e-9) / m_peak);
            m_level[b] = float(std::clamp(1 + db / RangeDb, 0.0, 1.0));
        }
        m_count = 0;
        m_fresh = true;
    }

    // True once since the last call when a window was completed.
    bool takeFresh() {
        bool was = m_fresh;
        m_fresh = false;
        return was;
    }
    float level(int band) const { return m_level[band]; }

private:
    // One second-order section; two of them make a fourth-order filter.
    struct Section {
        double b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0, z1 = 0, z2 = 0;
        void clear() { z1 = z2 = 0; }
        void design(double frequency, int rate, bool high) {
            const double pi = 3.14159265358979323846;
            double w0 = 2 * pi * std::min(frequency, 0.45 * rate) / rate;
            double alpha = std::sin(w0) / (2 * 0.70710678);
            double c = std::cos(w0), a0 = 1 + alpha;
            double edge = high ? (1 + c) / 2 : (1 - c) / 2;
            b0 = edge / a0;
            b1 = (high ? -(1 + c) : 1 - c) / a0;
            b2 = edge / a0;
            a1 = -2 * c / a0;
            a2 = (1 - alpha) / a0;
        }
        double process(double x) {  // transposed direct form II
            double y = b0 * x + z1;
            z1 = b1 * x - a1 * y + z2;
            z2 = b2 * x - a2 * y;
            return y;
        }
    };
    struct Filter {
        Section first, second;
        void clear() {
            first.clear();
            second.clear();
        }
        void design(double frequency, int rate, bool high) {
            first.design(frequency, rate, high);
            second.design(frequency, rate, high);
        }
        double process(double x) { return second.process(first.process(x)); }
    };
    int m_rate = 0, m_window = 1, m_count = 0;
    Filter m_low, m_midHigh, m_midLow, m_high;
    double m_sum[Bands] = {0, 0, 0}, m_peak = StartPeak;
    float m_level[Bands] = {0, 0, 0};
    bool m_fresh = false;
};
