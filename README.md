<!-- Copyright (c) 2026 Logic Magicians Software -->
<!-- SPDX-License-Identifier: GPL-3.0-only
     This program is free software: you can redistribute it and/or modify it
     under the terms of the GNU General Public License version 3 as published
     by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
     see the file LICENSE for the license text. -->
# Milkcrate Vinyl

**Your music. Your playlists. Your ratings. On your desktop and your phone,
and nobody can take them away.**

If you own a big collection of music in ordinary files, free of copy
protection and subscriptions, this is for you. Milkcrate Vinyl lets you build
playlists, rate your songs, and play them on your computer or your phone, much
as iTunes did in its early days, before music became something you rent.

It is open source and free, and it will stay that way. There is no account, no
subscription, and no company whose server has to stay up for your library to
keep working.

Your music files are never changed. Everything the program learns about your
songs (play counts, when you last played them, tempo, ratings, playlists) goes
into a separate database file, built on SQLite, a well-regarded and widely
used database system. The originals stay exactly as you got them.

A database editor is included for tidying a large collection. Every song
carries details such as its title, artist, album, genre and year, which music
programs call "tags". Collections picked up over the years are often a mess:
the same artist spelled three ways, a missing genre, an album with no name.
With the editor you select whole folders, albums or every song with a certain
artist or genre, and fix those details on all of them in one step.

The desktop and the phone share that database. A sync copies new music across
and brings the two copies of everything else together, so your ratings, play
counts and last-played dates are the same on both: one version of the truth.

## What you get

- Plays the files you already have: MP3, FLAC, Ogg, Opus, M4A, WAV and more.
- Never modifies your music files.
- Rates songs as unrated, ZERO stars, or 1 to 5 stars. Unrated and zero stars
  are different: unrated means "I haven't decided", zero means "I don't want to
  hear this".
- Remembers each song's play count, last played date and tempo (BPM).
- Playlists you build by hand, in the order you choose.
- Dynamic playlists that pick songs by rules, such as "jazz, rated 4 or more,
  not heard this month", and refill themselves as you listen.
- Timed playlists: play one playlist for 30 minutes, then another for an hour.
- A database editor for mass editing: fix the title, artist, album or genre of
  thousands of songs at once, and give a song several genres or artists.
- Finds duplicate copies of a song and keeps them out of your playlists.
- Searches by artist, album, song or genre, on the desktop and on the phone.
- Evens out loud and quiet songs with volume leveling.
- An equalizer on the phone, with presets for rock, metal, jazz and other
  kinds of music, that can pick the preset from each song's genre.
- An Android app that plays in the background, with lock-screen controls and
  rating.
- Syncs ratings, play counts, last-played dates and new music between the
  desktop and the phone over Wi-Fi.
- Free software under the GNU General Public License version 3.

## The parts

- **Player**: `scripts/play`. Plays your library on the desktop.
- **Database editor**: `scripts/edit-database`. Mass editing for large
  collections: fix song details, find duplicates, manage playlists.
- **Android app**: "Milkcrate Vinyl", built by `scripts/build-for-android`.
- **Sync**: File > Sync with Phone in the player or editor, or
  `scripts/sync-phone`.
- **Setup tools**: `scripts/initialize-database` builds the database from your
  music folder; `scripts/change-music-root-directory` follows a moved folder and
  `scripts/show-music-root-directory` prints the folder the database records.

## Quick start

The desktop programs run on Linux and need python3 and network access on the
first run. See [Software prerequisites][d00].

```
source scripts/setup --venv ~/music-venv --bod ~/music-bod --database ~/music.db
scripts/initialize-database --root ~/Music
scripts/play
```

`scripts/setup` records the settings in environment variables for the current
shell. The virtual environment is created by the first script that needs it.

To build and install the Android app:

```
scripts/build-for-android
```

The build installs its own toolchain on the first run (several GB) and writes
the APK to `${MILKCRATEVINYL_BOD}/milkcrate-vinyl-arm64_v8a.apk`.

## Documentation

The documentation is in [docs/](docs/), as plain text, to be read in order.

0. [Software prerequisites][d00]
1. [Configuring the shell environment][d01]
2. [Creating the database][d02]
3. [Editing the database][d03]
4. [Static playlists][d04]
5. [Dynamic playlists][d05]
6. [Playlist containers and sequences][d06]
7. [Song rating][d07]
8. [Building the Android version][d08]
9. [Installing on the phone][d09]
10. [Running the software][d10]
11. [Getting the library onto the phone][d11]
12. [Searching on the phone][d12]
13. [Rating on the phone][d13]
14. [Playlists on the phone][d14]
15. [Sound on the phone][d15]

[d00]: docs/00-software-prerequisites.txt
[d01]: docs/01-configuring-the-shell.txt
[d02]: docs/02-creating-a-database.txt
[d03]: docs/03-editing-the-database.txt
[d04]: docs/04-static-playlists.txt
[d05]: docs/05-dynamic-playlists.txt
[d06]: docs/06-playlist-containers.txt
[d07]: docs/07-song-rating.txt
[d08]: docs/08-building-the-android-version.txt
[d09]: docs/09-installing-on-the-phone.txt
[d10]: docs/10-running-the-software.txt
[d11]: docs/11-getting-the-library-onto-the-phone.txt
[d12]: docs/12-searching-on-the-phone.txt
[d13]: docs/13-rating-on-the-phone.txt
[d14]: docs/14-playlists-on-the-phone.txt
[d15]: docs/15-sound-on-the-phone.txt

## Project layout

```
desktop/   Python programs: player, editor, database and sync code
android/   Android app (C++/QML) and the desktop-side phone tools
scripts/   Command wrappers; run these, not the Python files directly
docs/      Documentation
WORKLIST.md  Work in progress and design notes
```

## License

GNU General Public License version 3. See [LICENSE](LICENSE).
