// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
#include "equalizer.h"

#include <algorithm>
#include <cmath>

constexpr double Equalizer::Frequencies[Equalizer::Bands];
static constexpr double Knee = 0.9;

void Equalizer::configure(double sampleRate, const double *gainsDb) {
    const double q = 0.9;
    const double pi = 3.14159265358979323846;
    m_active = 0;
    for (int i = 0; i < Bands; ++i) {
        Biquad &b = m_band[i];
        b = Biquad();
        double g = std::clamp(gainsDb[i], -MaxGainDb, MaxGainDb);
        if (g == 0 || Frequencies[i] >= 0.45 * sampleRate) continue;
        double a = std::pow(10.0, g / 40.0);
        double w0 = 2 * pi * Frequencies[i] / sampleRate;
        double alpha = std::sin(w0) / (2 * q);
        double c = std::cos(w0);
        double a0 = 1 + alpha / a;
        b.b0 = (1 + alpha * a) / a0;
        b.b1 = -2 * c / a0;
        b.b2 = (1 - alpha * a) / a0;
        b.a1 = -2 * c / a0;
        b.a2 = (1 - alpha / a) / a0;
        // the active filters are kept first, in order
        m_band[m_active++] = b;
    }
}

void Equalizer::reset() {
    for (Biquad &b : m_band)
        for (int ch = 0; ch < 2; ++ch) b.z1[ch] = b.z2[ch] = 0;
}

void Equalizer::process(float *samples, int frames) {
    if (m_active == 0) return;
    for (int f = 0; f < frames; ++f) {
        for (int ch = 0; ch < 2; ++ch) {
            double x = samples[f * 2 + ch];
            for (int i = 0; i < m_active; ++i) {
                Biquad &b = m_band[i];
                double y = b.b0 * x + b.z1[ch];
                b.z1[ch] = b.b1 * x - b.a1 * y + b.z2[ch];
                b.z2[ch] = b.b2 * x - b.a2 * y;
                x = y;
            }
            double m = std::fabs(x);
            if (m > Knee)
                x = std::copysign(Knee + (1 - Knee) * std::tanh((m - Knee) /
                                                                (1 - Knee)),
                                  x);
            samples[f * 2 + ch] = float(x);
        }
    }
}
