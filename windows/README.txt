Copyright (c) 2026 Logic Magicians Software
Released under the GNU General Public License version 3; see the file
LICENSE.

MILKCRATE VINYL FOR WINDOWS
===========================

This package holds the desktop programs (the player, the database editor and
the database tools), the Windows scripts that start them, the program for
an Android phone (milkcrate-vinyl-arm64_v8a.apk) and the documents (docs).
Nothing is installed: unzip the package into a folder and use it there.

The desktop programs are written in Python.  They were developed on Linux;
these scripts and the changes for Windows have not been run on Windows yet.
Expect to meet problems, and report them.

What to install first
---------------------

Required:

  * Windows 10 or 11.
  * Python 3 from https://python.org, 3.10 or later.  In the installer tick
    "Add python.exe to PATH" (or leave the "py" launcher on, which is the
    default).
  * Network access to the Python package index the first time: the first
    script creates the virtual environment and installs PyQt6, mutagen,
    numpy, pyacoustid and musicbrainzngs into it.

Optional:

  * ffmpeg and ffprobe on the PATH: volume leveling, tempo detection, the
    ringtone samples and find-stalled-songs need them.
  * adb (Android platform-tools) on the PATH, or its path in the environment
    variable MILKCRATEVINYL_ADB: only for putting a database on the phone by
    USB cable.  The sync over Wi-Fi does not need it.
  * chromaprint (fpcalc) and an AcoustID key: only for tagging songs from
    their sound.

First use
---------

Open a Command Prompt in the folder of the package and run:

    scripts\setup --venv C:\MilkcrateVinyl\venv ^
        --database C:\MilkcrateVinyl\music.db --save

The two names are where the Python environment and the library database go;
use other folders if you like.  "--save" keeps them for every Command Prompt
opened later; without it they last for this window only.  Then:

    scripts\initialize-database --root D:\Music
    scripts\play

The first script that needs the Python environment creates it (a minute or
two).  scripts\create-venv makes or repairs it by hand.  The documents in docs
describe each program; their examples show the Linux scripts, which have the
same names as the .bat files here, so "scripts/play" there is "scripts\play"
here.

The scripts
-----------

  setup                           records the environment and the database
  create-venv                     creates or repairs the Python environment
  initialize-database --root DIR  creates the library database
  play                            the player
  edit-database                   the database editor
  find-stalled-songs              finds songs that stall on the phone
  change-music-root-directory     moves the library to another folder
  show-music-root-directory       shows where the library is
  sync-phone                      syncs with a phone from the command line

Each takes --database FILE to use another database than the one recorded.

Putting the program on a phone
------------------------------

Copy milkcrate-vinyl-arm64_v8a.apk to the phone (a cable and File Explorer, or
any other way), open it on the phone and allow the installation from that
source.  docs\09-installing-on-the-phone.txt and
docs\11-getting-the-library-onto-the-phone.txt tell the rest.  The phone's
program is for 64-bit ARM phones with Android 8 or later (API 26).

What differs from Linux
-----------------------

  * There are no scripts for building the phone's program: it is built on
    Linux, and the package holds the result.
  * The pairing with a phone over Wi-Fi finds the phone by the Windows route
    table.  If the phone is not found, give its address with --host
    (sync-phone does).
