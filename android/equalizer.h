// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
//
// Five-band equalizer for interleaved stereo float samples: one peaking
// filter per band (RBJ biquads), so a gain of 0 dB leaves a band untouched.
#pragma once

class Equalizer {
public:
    static constexpr int Bands = 5;
    static constexpr double Frequencies[Bands] = {60, 230, 910, 3600, 14000};
    static constexpr double MaxGainDb = 12;

    // gainsDb: Bands values in dB (-MaxGainDb to MaxGainDb); a band whose
    // centre is too close to the sample rate's limit is left out.  Peaks
    // above 0.9 are rounded off so a boost cannot clip.
    void configure(double sampleRate, const double *gainsDb);
    void reset();  // forget the filter memory (after a seek)
    bool flat() const { return m_active == 0; }
    void process(float *samples, int frames);  // samples: frames * 2

private:
    struct Biquad {
        double b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0;
        double z1[2] = {0, 0}, z2[2] = {0, 0};
    };
    Biquad m_band[Bands];
    int m_active = 0;
};
