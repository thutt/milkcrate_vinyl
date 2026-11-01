Copyright (c) 2026 Logic Magicians Software
SPDX-License-Identifier: GPL-3.0-only
This program is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License version 3 as published
by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
see the file LICENSE for the license text.

MILKCRATE VINYL FOR WINDOWS - READ THIS FIRST
=============================================

Milkcrate Vinyl is a music player for a library of your own files.  This
package holds:

  * the desktop programs (the player, the database editor and the database
    tools), which run on Windows, and the scripts that start them;
  * the program for an Android phone, milkcrate-vinyl-arm64_v8a.apk, which
    plays the same library on the phone;
  * the documents, in the folder docs.

Nothing is installed by the package.  Unzip it into a folder, for example
C:\MilkcrateVinyl\program, and use it there.

The desktop programs were developed on Linux.  The scripts and the changes
for Windows have not been run on Windows yet.  Expect to meet problems, and
report them.

In short
--------

  1. Install Python 3.10 or later (Part 1).
  2. Run scripts\setup, then scripts\initialize-database --root YOUR-MUSIC,
     then scripts\play (Part 2).
  3. Put the APK on the phone and install it (Part 3).
  4. Start the app and allow what it asks for (Part 4).
  5. Send the database and the music to the phone with the Wi-Fi sync, or
     with a cable (Part 5).


Part 1.  What to install on Windows
-----------------------------------

Required:

  * Windows 10 or 11.
  * Python 3 from https://python.org, version 3.10 or later.  In the
    installer tick "Add python.exe to PATH".  (Leaving the "py" launcher
    on, which is the default, also works.)
  * Network access the first time you start a program: the first script
    creates a Python environment and installs PyQt6, mutagen, numpy,
    pyacoustid and musicbrainzngs into it from the Python package index.

Optional:

  * ffmpeg and ffprobe on the PATH: volume leveling, tempo detection, the
    ringtone samples and find-stalled-songs need them.
  * adb (Google's "SDK Platform-Tools" for Windows), unzipped anywhere:
    only for installing the phone's program, or syncing, over a USB cable
    (Parts 3 and 5).  Either put its folder on the PATH, or set the
    variable MILKCRATEVINYL_ADB to the full name of adb.exe:

        setx MILKCRATEVINYL_ADB "C:\platform-tools\adb.exe"

    (open a new Command Prompt afterwards).
  * chromaprint (fpcalc) and an AcoustID key: only for tagging songs from
    their sound.


Part 2.  Setting up the desktop programs
----------------------------------------

Open a Command Prompt in the folder of the package and run:

    scripts\setup --venv C:\MilkcrateVinyl\venv ^
        --database C:\MilkcrateVinyl\music.db --save

The two names are where the Python environment and the library database go;
use other folders if you like.  "--save" keeps the names for every Command
Prompt you open later; without it they last for this window only.  Run
"scripts\setup" with no arguments to see the current settings.

Then make the library database from your music folder, and start the player:

    scripts\initialize-database --root D:\Music
    scripts\play

The first script that needs the Python environment creates it, which takes a
minute or two.  scripts\create-venv makes or repairs it by hand.

The numbered documents in docs show Linux commands.  The scripts here have
the same names, so "scripts/play" there is "scripts\play" here.


Part 3.  Putting the program on the phone
-----------------------------------------

The phone's program is for 64-bit ARM phones with Android 8 or later, which
is nearly every phone made since 2018.  There are two ways to install it,
and a third to use only when the first does not work.

Way A: copy the file with Windows (no extra software)

  1. Plug the phone into the PC with a USB cable and unlock the phone.
  2. Pull down the notification area on the phone, tap the USB notification
     ("Charging this device via USB" or similar) and choose "File transfer"
     (also called "MTP" or "Transferring files").  Charging only does not
     work: Windows would show no files.
  3. Open File Explorer.  The phone appears under "This PC".  Open it, then
     "Internal storage" (or "Phone"), then the folder "Download".
  4. Copy milkcrate-vinyl-arm64_v8a.apk from the package into that folder.
  5. On the phone open the app "Files" (or "My Files"), then "Downloads",
     and tap milkcrate-vinyl-arm64_v8a.apk.
  6. If Android says the phone is not allowed to install apps from this
     source, tap "Settings", switch on "Allow from this source", press Back,
     and tap "Install".
  7. If Google Play Protect warns that the app is unknown, tap "More
     details" and "Install anyway".  The app is not from the Play Store.
  8. Tap "Open", or find "Milkcrate Vinyl" among the apps.

Way B: adb over the cable

This needs adb (Part 1) and USB debugging on the phone: Settings > About
phone, tap "Build number" seven times, then Settings > Developer options >
USB debugging.  Connect the cable and accept "Allow USB debugging" on the
phone.  In a Command Prompt in the folder of the package:

    adb devices
    adb install -r milkcrate-vinyl-arm64_v8a.apk

"adb devices" must list the phone as "device".  "unauthorized" means the
question on the phone was not answered.  If you set MILKCRATEVINYL_ADB
instead of using the PATH, type the full name of adb.exe in place of "adb".
With more than one phone or emulator connected, add -s SERIAL after adb,
using a serial from the list.

Way C: download the file on the phone (a fallback, at your own risk)

Use this only if Windows 11 does not let you put files on the phone, that
is, if Way A fails (for example, Paste is missing in the phone's folder, or
dragging a file shows a slashed circle) and you do not want to use adb.  The
PC makes the APK available over the Wi-Fi, and the phone's browser downloads
it.  Nothing is copied by Windows.

  1. Put the PC and the phone on the same Wi-Fi network.  Use a network you
     trust, never a public one (a cafe, a hotel, a school).
  2. On the PC make a new empty folder, for example C:\MvShare, and copy
     milkcrate-vinyl-arm64_v8a.apk into it.  Nothing else may be in it.
  3. Find the PC's address: open a Command Prompt and run "ipconfig".  Use
     the "IPv4 Address" of the Wi-Fi adapter, for example 192.168.1.23.
  4. In the Command Prompt go to that folder and start the server.  Use the
     Python of the environment made in Part 2:

         cd C:\MvShare
         C:\MilkcrateVinyl\venv\Scripts\python.exe -m http.server 8000

     (Use the folder you gave to "scripts\setup --venv".  If "python" works
     in the Command Prompt, "python -m http.server 8000" is the same.  The
     "py" launcher is not always installed.)  Windows Defender Firewall may
     ask whether Python may use the network: allow it on private networks
     only.
  5. On the phone open the browser and go to http://192.168.1.23:8000 (with
     the address from step 3).  Tap milkcrate-vinyl-arm64_v8a.apk.  If the
     browser warns that this type of file can harm the device, tap
     "Download anyway" or "Keep".
  6. When the download ends, tap "Open" in the browser's message or in the
     "Downloads" list, and go on as in steps 6 to 8 of Way A.
  7. Back on the PC, press Ctrl+C in the Command Prompt to stop the server
     as soon as the download is finished.  Delete C:\MvShare if you like.

Why this is at your own risk.  The server is a general-purpose test server
of Python, not a program made for this package, and it was not tested on
Windows here.

  * It gives every file in its folder to any device that can reach the PC
    on the network, without a password and without asking.  That is why the
    folder must hold only the APK, and why the network must be one you
    trust: another device on the same Wi-Fi can fetch the files and see the
    list of them.
  * The traffic is not encrypted, and the phone cannot check that what it
    receives is really the file you put there.  A device on the same network
    that is up to no good could in principle answer in its place, and an app
    that you install has access to the phone's music and files.  The APK is
    not from the Play Store, so Android and Google Play Protect cannot vouch
    for it.
  * While the server runs, a port of the PC (8000) is open and the firewall
    has an exception for Python.  Forgetting to stop the server leaves both
    in place.

If you are not sure, use Way A or Way B.

Updating: do the same with a newer APK.  The data of the app is kept, if the
new APK is signed with the same key as the installed one.  Android refuses a
different key; uninstalling the old copy then removes the app's own data.
A library in the folder MusicLibrary on the SD card (Part 4) is kept.


Part 4.  The first run on the phone
-----------------------------------

The app asks for permission to read audio files and to show notifications:
allow both.  If the app shows an orange banner "Allow access to files", tap
the button and switch on "Milkcrate Vinyl" in the list that opens, then go
back to the app.

The app looks for its library, the database tags.db and the music, in the
folder MusicLibrary of the SD card, or of the internal storage if the phone
has no card.  Until a sync has put a library there the song list is empty.
"..." (the button in the top bar) > Library shows the folders in use.


Part 5.  Getting the library onto the phone
-------------------------------------------

The first sync copies the whole library, so make sure the phone has room.  The
sync also brings the plays and ratings made on the phone back to the desktop,
so use it again from time to time.

Wi-Fi sync, from the player

  1. Put the PC and the phone on the same Wi-Fi network, one you trust: the
     sync is not encrypted.
  2. On the phone: tap "..." > "Sync with the desktop..." and tap Start.  The
     app shows a pairing code, its address and the port (8765).  Leave the
     app open.
  3. On the PC, in the player: File > Sync with Phone...
  4. Type the pairing code and click Sync.  Uncheck the music box to send the
     database only.

From a Command Prompt, with the player and the editor closed:

    scripts\sync-phone --code CODE
    scripts\sync-phone --code CODE --no-music

If the phone is not found, give its address:

    scripts\sync-phone --code CODE --host 192.168.1.23

Windows Defender Firewall may ask whether Python may use the network the
first time: allow it on private networks.  Public-network profiles block the
phone's answers.  Some Wi-Fi routers keep devices apart ("AP isolation" or
"guest network"); the sync does not work through them.

Cable sync with adb

This needs adb and USB debugging (Part 3, Way B).  The folder on the phone
is the one the app uses; "adb shell ls /storage" lists the cards, and a
card is named like XXXX-XXXX:

    scripts\sync-phone --adb --dest /storage/XXXX-XXXX/MusicLibrary

Without a card use /storage/emulated/0/MusicLibrary.  Close the app first;
the script stops it.  docs\11-getting-the-library-onto-the-phone.txt tells
the rest, including the review of the changes made on the phone.


Part 6.  The scripts
--------------------

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


Part 7.  If the player makes no sound
-------------------------------------

The player has no setting for the sound output.  It plays through the
playback device that Windows has chosen as the default, and it picks the
device when it starts.  When a song plays but nothing is heard, check:

  * Settings > System > Sound > Output: is the device you listen with, for
    example "Headphones" or "Speakers", the chosen one?  A PC often lists
    its headphone jack, its speakers and the sound of a monitor or TV
    (HDMI or DisplayPort) as separate devices, and the default may be one
    of the others.
  * A monitor or TV that is the default device can be silent although
    Windows shows it as working.  Some monitors send no sound over HDMI
    while their power saving is on, and the sound comes back when it is
    off in the monitor's own menu.  Choosing another default device avoids
    the problem.
  * Settings > System > Sound > Volume mixer (or Advanced sound options):
    python.exe may be sent to another device than the default, or its
    slider may be at zero.
  * After plugging in headphones or changing the default device, close and
    start the player again.
  * A sound driver with its own panel (Realtek, for example) may need the
    plugged-in jack to be named there.
  * Play something in another program, such as a browser, with the same
    device.  If that is silent too, the cause is in Windows or the device,
    not in this program.


Where to read more
------------------

  docs\09-installing-on-the-phone.txt           the phone's program
  docs\11-getting-the-library-onto-the-phone.txt  the sync, in full
  docs\13-rating-on-the-phone.txt               rating songs after a drive
  docs\14-playlists-on-the-phone.txt            playlists and the car display
  docs\15-sound-on-the-phone.txt                the equalizer and the volume

What differs from Linux
-----------------------

  * There are no scripts for building the phone's program: it is built on
    Linux, and the package holds the result.
  * The Wi-Fi sync finds the phone with the Windows route table.  If the
    phone is not found, give its address with --host.
  * The numbered documents show Linux commands; use the .bat scripts with
    the same names.
