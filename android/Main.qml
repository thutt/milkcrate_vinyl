// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: window
    visible: true
    width: 400; height: 800
    title: "Milkcrate Vinyl"
    Material.theme: Material.System
    // Colours that people with red-green or blue-yellow colour blindness can
    // tell apart from the text and the background (the Okabe-Ito blue and
    // orange), in a light and a dark form; each has at least 4.5:1 contrast
    // with the background.  The stars also differ in shape, not only colour.
    readonly property bool dark: Qt.styleHints.colorScheme === Qt.Dark
    readonly property color accentInk: dark ? "black" : "white"
    readonly property color starColor: dark ? "#E69F00" : "#A15A00"
    readonly property color errorColor: dark ? "#FFA366" : "#B34700"
    Material.accent: dark ? "#56B4E9" : "#0072B2"
    // the phone's Back: from a playlist to the list of playlists, from there to
    // the songs
    onClosing: function(close) {
        if (tab === 1 && entriesOpen) {
            close.accepted = false
            entriesOpen = false
        }
        else if (tab === 1) { close.accepted = false; tab = 0 }
        // the top: hide the app like Home does (closing the window would leave
        // the music playing in an app that cannot be reopened)
        else if (Qt.platform.os === "android") {
            close.accepted = false
            player.moveToBack()
        }
    }

    property int tab: 0                       // 0 songs, 1 playlists
    // the app opens on the Playlists tab when there are playlists
    property bool startTabChosen: false
    // rows showing the playing song (they carry its stars)
    function accentAlpha(a) {
        return Qt.rgba(Material.accent.r, Material.accent.g,
                       Material.accent.b, a)
    }
    function foregroundAlpha(a) {
        return Qt.rgba(Material.foreground.r, Material.foreground.g,
                       Material.foreground.b, a)
    }

    property int playingRows: 0
    // the playlist the play button starts when nothing is playing (the first
    // one to begin with)
    property int selectedList: 0
    // a playlist's tracks are shown on the playlists tab
    property bool entriesOpen: false
    property string sortKey: "artist"
    readonly property var sortNames: ({
        artist: "Artist", title: "Title", album: "Album", path: "Folder"
    })

    function clock(ms) {
        var s = Math.max(0, Math.floor(ms / 1000));
        return Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60);
    }


    // The rating of the playing song.  A song that is not rated shows only
    // "unrated" (tap it to start rating: zero stars).
    // A rated song shows a circle with its number of stars and the stars: a
    // star sets that many, the one that is set sets
    // zero.  The circle steps back: from 1 to 5 stars to zero, and from zero to
    // unrated.
    // Shuffle: the usual two crossing arrows.  Off, they are a plain outline
    // with "shuffle off" under them; on, they sit on a
    // filled accent circle with "SHUFFLE ON" in bold, so the state cannot be
    // missed.
    component ShuffleButton: Item {
        id: sh
        property bool on: false
        property bool available: true  // a dynamic playlist plays in order
        signal toggled(bool value)
        implicitWidth: 76; implicitHeight: 68
        opacity: available ? 1 : 0.4
        Rectangle {
            id: disc
            width: 44; height: 44; radius: 22
            anchors.horizontalCenter: parent.horizontalCenter
            color: sh.on ? Material.accent : "transparent"
            border.width: sh.on ? 0 : 1.5
            border.color: window.foregroundAlpha(0.45)
            Canvas {
                id: glyph
                width: 24; height: 24
                anchors.centerIn: parent
                property color ink: sh.on ? window.accentInk
                                          : window.foregroundAlpha(0.75)
                onInkChanged: requestPaint()
                onPaint: {
                    var c = getContext("2d");
                    c.reset();
                    c.strokeStyle = ink; c.fillStyle = ink;
                    c.lineWidth = 2.2;
                    c.lineCap = "round";
                    c.lineJoin = "round";
                    // bottom left up to top right
                    c.beginPath();
                    c.moveTo(2.5, 18);
                    c.lineTo(6, 18);
                    c.bezierCurveTo(12, 18, 12, 6, 17, 6);
                    c.stroke();
                    // top left down to bottom right, broken where they cross
                    c.beginPath();
                    c.moveTo(2.5, 6);
                    c.lineTo(6, 6);
                    c.bezierCurveTo(8.5, 6, 9.7, 7.4, 10.6, 9);
                    c.moveTo(13.4, 15);
                    c.bezierCurveTo(14.3, 16.6, 15.5, 18, 18, 18);
                    c.stroke();
                    c.beginPath();
                    c.moveTo(16.5, 2.5);
                    c.lineTo(21.5, 6);
                    c.lineTo(16.5, 9.5);
                    c.closePath();
                    c.fill();
                    c.beginPath();
                    c.moveTo(16.5, 14.5);
                    c.lineTo(21.5, 18);
                    c.lineTo(16.5, 21.5);
                    c.closePath();
                    c.fill();
                }
            }
        }
        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: disc.bottom; anchors.topMargin: 3
            text: sh.on ? "SHUFFLE ON" : sh.available ? "shuffle off" : "in order"
            font.pixelSize: 11
            font.bold: sh.on
            font.letterSpacing: sh.on ? 0.8 : 0
            color: sh.on ? Material.accent : window.foregroundAlpha(0.6)
        }
        MouseArea {
            anchors.fill: parent; enabled: sh.available
            onClicked: sh.toggled(!sh.on)
        }
    }

    component Stars: Row {
        spacing: 2
        Label {
            visible: player.rating < 0
            text: "unrated"
            font.pixelSize: 15; font.italic: true; opacity: 0.7
            padding: 8
            MouseArea { anchors.fill: parent; onClicked: player.rate(0) }
        }
        Rectangle {
            visible: player.rating >= 0
            width: 30; height: 30; radius: 15
            anchors.verticalCenter: parent.verticalCenter
            color: Material.accent
            Label {
                anchors.centerIn: parent
                text: player.rating
                font.pixelSize: 15
                font.bold: true
                color: window.accentInk
            }
            MouseArea {
                anchors.fill: parent
                anchors.margins: -4
                onClicked: {
                    if (player.rating > 0) player.rate(0)
                    else player.clearRating()
                }
            }
        }
        Repeater {
            model: 5
            Label {
                visible: player.rating >= 0
                text: index < player.rating ? "\u2605" : "\u2606"
                font.pixelSize: 28
                color: index < player.rating ?
                       window.starColor : Material.foreground
                opacity: index < player.rating ? 1 : 0.6
                padding: 3
                MouseArea {
                    anchors.fill: parent
                    onClicked: player.rate(index + 1)
                }
            }
        }
    }

    // Three bars beside the song that plays.  Each shows how loud one part of
    // the sound is: the low, the middle and the high.  The levels are measured
    // from the sound itself (player.level) while it goes through the equalizer
    // path.  With the equalizer off nothing is measured, and the bars glide to
    // random heights in time with the song's BPM instead.  A bar rises at once
    // and falls at a steady speed; paused, the bars deflate to the lowest
    // height.
    component Equalizer: Item {
        id: eq
        property bool running: false
        property bool live: false    // the levels are being measured
        readonly property real lowest: 3
        readonly property real highest: 22
        Layout.preferredWidth: 22; Layout.preferredHeight: 22
        onRunningChanged: {
            for (var i = 0; i < 3; ++i) {
                var b = bars.itemAt(i)
                if (!b) continue
                if (running) b.stopMotion()
                else b.startDeflate()
            }
        }
        // the levels, read about thirty times a second
        Timer {
            interval: 33
            repeat: true
            running: eq.running
            triggeredOnStart: true
            onTriggered: {
                eq.live = player.levelsLive()
                if (!eq.live) return
                for (var i = 0; i < 3; ++i)
                    bars.itemAt(i).follow(player.level(i))
            }
        }
        Row {
            anchors.bottom: parent.bottom
            spacing: 3
            Repeater {
                id: bars
                model: 3
                Rectangle {
                    id: bar
                    width: 5; radius: 1
                    // the accent's hue a little deeper than the accent itself,
                    // so that the tip can be a clearly more intense shade of
                    // the same colour (a lighter one would be white)
                    color: Qt.hsla(Material.accent.hslHue, 0.75,
                                   window.dark ? 0.50 : 0.30, 1)
                    anchors.bottom: parent.bottom
                    height: 3
                    // the part above 15 px is the same hue at full
                    // saturation, like the peak of a meter
                    Rectangle {
                        anchors.top: parent.top
                        width: parent.width; radius: 1
                        height: Math.max(0, parent.height - 15)
                        color: Qt.hsla(Material.accent.hslHue, 1.0,
                                       window.dark ? 0.62 : 0.48, 1)
                    }
                    function stopMotion() {
                        glide.stop()
                        deflate.stop()
                    }
                    // Paused: fall to the lowest height at a steady speed, the
                    // way a real display does.
                    function startDeflate() {
                        deflate.duration = 100 + (bar.height - 3) * 45
                        deflate.restart()
                    }
                    // Measured: rise at once to the level, fall 0.9 px a tick.
                    function follow(level) {
                        stopMotion()
                        var to = eq.lowest + level * (eq.highest - eq.lowest)
                        height = to > height ? height + (to - height) * 0.6
                                             : Math.max(to, height - 0.9)
                    }
                    Connections {
                        target: eq
                        function onLiveChanged() {
                            if (eq.live) bar.stopMotion()
                        }
                    }
                    // Not measured: glide to a new random height; the farther
                    // it goes, the longer it takes, so a big jump is not
                    // abrupt.
                    NumberAnimation {
                        id: glide
                        target: bar
                        property: "height"
                        easing.type: Easing.InOutQuad
                    }
                    NumberAnimation {
                        id: deflate
                        target: bar
                        property: "height"
                        to: 3
                        easing.type: Easing.Linear
                    }
                    // Each bar moves on its own division of the beat (once a
                    // beat, twice a beat, three times in four), so they stay
                    // in time with the song and still do not move together.
                    Timer {
                        running: eq.running && !eq.live
                        repeat: true
                        triggeredOnStart: true
                        interval: 140
                        onTriggered: {
                            var step = 60000 / player.bpm *
                                       [1, 0.5, 0.75][index]
                            var to = 4 + Math.random() * 18
                            glide.stop()
                            glide.to = to
                            glide.duration = Math.min(
                                180 + Math.abs(to - bar.height) * 28,
                                step * 0.85)
                            glide.restart()
                            interval = step
                        }
                    }
                }
            }
        }
    }

    // One song in a list.  The song that is playing stands out: tinted row, a
    // bar down its left edge, a bouncing
    // equalizer (still when paused) and its title in the accent colour; the
    // songs after it are plain.
    component TrackRow: ItemDelegate {
        id: row
        property bool playingNow: false
        property bool marked: false
        property string heading
        property string sub
        property string detail    // why a dynamic playlist chose the song
        property bool reserve: false  // room for it before it arrives
        property int detailHeight: 34
        property int rating: -1   // stars of a song that is not playing
        width: ListView.view.width
        height: (playingNow ? 104 : 64) + (detail !== "" || reserve ? detailHeight : 0)
        Behavior on height { NumberAnimation { duration: 150 } }
        property bool counted: false
        function syncCount() {
            var want = playingNow && visible;
            if (want !== counted) {
                counted = want
                window.playingRows += want ? 1 : -1
            }
        }
        onPlayingNowChanged: syncCount()
        onVisibleChanged: syncCount()
        Component.onCompleted: syncCount()
        Component.onDestruction: if (counted) window.playingRows -= 1
        background: Rectangle {
            color: row.playingNow ? window.accentAlpha(0.16) :
                   row.down ? Qt.rgba(0.5, 0.5, 0.5, 0.15) :
                   row.marked ? Qt.rgba(0.5, 0.5, 0.5, 0.10) : "transparent"
            Rectangle {
                visible: row.marked
                width: 3
                height: parent.height
                color: window.accentAlpha(0.6)
            }
            Rectangle {
                visible: row.playingNow
                width: 5; height: parent.height
                color: Material.accent
            }
        }
        contentItem: RowLayout {
            spacing: 10
            Equalizer {
                visible: row.playingNow
                running: row.playingNow && player.playing
                Layout.leftMargin: 4
                Layout.alignment: Qt.AlignTop
                Layout.topMargin: 13
            }
            Column {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                Label {
                    text: row.heading
                    width: parent.width
                    elide: Text.ElideRight
                    font.pixelSize: 16; font.bold: row.playingNow
                    color: row.playingNow ? Material.accent
                                          : Material.foreground
                }
                Label {
                    text: row.sub; width: parent.width; elide: Text.ElideRight
                    font.pixelSize: 13; opacity: row.playingNow ? 0.85 : 0.65
                }
                Label {
                    visible: row.detail !== ""
                    text: row.detail; width: parent.width
                    wrapMode: Text.Wrap; maximumLineCount: 2
                    elide: Text.ElideRight
                    font.pixelSize: 12; opacity: 0.8
                    color: Material.accent
                }
                Stars { visible: row.playingNow }
            }
            // the rating of the other songs: five stars, none when not rated
            Label {
                visible: !row.playingNow && row.rating >= 0
                Layout.alignment: Qt.AlignVCenter
                Layout.rightMargin: 4
                text: row.rating < 0 ? "" :
                      "\u2605".repeat(row.rating) +
                      "\u2606".repeat(5 - row.rating)
                font.pixelSize: 15
                color: window.starColor
            }
        }
    }

    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 8; anchors.rightMargin: 4
            ToolButton {
                visible: tab === 1 && entriesOpen
                text: "←"
                font.pixelSize: 24
                onClicked: entriesOpen = false
            }
            Label {
                visible: tab === 1
                Layout.fillWidth: true
                text: entriesOpen ? player.openName : "Playlists"
                font.pixelSize: 18; elide: Text.ElideRight
            }
            // a dynamic playlist: how many library tracks its rules select
            Label {
                visible: tab === 1 && entriesOpen && player.openMatched >= 0
                text: player.openMatched + " matched"
                // the accent colour is hard to read on the toolbar
                font.pixelSize: 14; font.bold: true; color: "white"
            }
            TextField {
                id: searchField
                visible: tab === 0
                Layout.fillWidth: true
                placeholderText:
                    "Artist, album, song or genre (" + tracks.total + ")"
                inputMethodHints: Qt.ImhNoPredictiveText
                onTextChanged: searchTimer.restart()
                Timer {
                    id: searchTimer
                    interval: 200
                    onTriggered: player.search(searchField.text)
                }
            }
            ToolButton {
                visible: tab === 0
                text: sortNames[sortKey]
                onClicked: sortMenu.open()
                Menu {
                    id: sortMenu
                    Repeater {
                        model: ["artist", "title", "album", "path"]
                        MenuItem {
                            text: "Sort by " +
                                  sortNames[modelData].toLowerCase()
                            onTriggered: {
                                sortKey = modelData
                                player.sortBy(modelData)
                            }
                        }
                    }
                }
            }
            // a button of its own: the equalizer is used while listening.  It
            // is in the accent colour while it is on
            ToolButton {
                text: "EQ"
                font.bold: true
                font.pixelSize: 15
                palette.buttonText: player.eqOn ? Material.accent
                                                : Material.foreground
                onClicked: equalizer.open()
            }
            ToolButton {
                text: "⋮"
                font.pixelSize: 22
                onClicked: mainMenu.popup()
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Pane {
            Layout.fillWidth: true
            visible: player.needsFilesAccess
            Material.background:
                Material.color(Material.Orange, Material.Shade100)
            ColumnLayout {
                anchors.fill: parent
                Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    color: "black"
                    text: "Music needs permission to use all files, " +
                          "so that it can read the library and its " +
                          "database on the SD card."
                }
                Button {
                    text: "Allow access to files"
                    onClicked: player.requestFilesAccess()
                }
            }
        }

        TabBar {
            Layout.fillWidth: true
            currentIndex: tab
            onCurrentIndexChanged: tab = currentIndex
            TabButton { text: "Songs" }
            // the tab again: back to the list of playlists
            TabButton {
                text: (player.playingList >= 0 ? "\u266B " : "") +
                      "Playlists (" + playlists.count + ")"
                onClicked: entriesOpen = false
            }
        }

        Label {
            Layout.fillWidth: true
            Layout.margins: 12
            visible: tab === 0 && tracks.count === 0
            text: tracks.total === 0 ? player.message : "Nothing matches"
            wrapMode: Text.Wrap
            opacity: 0.7
        }

        ListView {
            id: list
            visible: tab === 0
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            model: tracks
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { }
            delegate: TrackRow {
                playingNow: trackId === player.currentId
                heading: title
                sub: artist + (album ? "  ·  " + album : "")
                rating: model.rating
                detail: model.detail
                reserve: true
                detailHeight: 20
                onClicked: { player.playRow(index); searchField.focus = false }
            }
        }

        // the playlists, containers and sequences
        ListView {
            visible: tab === 1 && !entriesOpen
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            model: playlists
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { }
            section.property: "kind"
            section.delegate: Label {
                width: ListView.view.width
                leftPadding: 16; topPadding: 8; bottomPadding: 4
                text: ({
                    sequence: "Sequences", container: "Containers",
                    playlist: "Playlists", dynamic: "Playlists"
                })[section]
                font.pixelSize: 13; font.bold: true; opacity: 0.6
            }
            delegate: ItemDelegate {
                id: listRow
                // the one the music comes from
                readonly property bool active: playlistId === player.playingList
                readonly property bool marked:
                    index === window.selectedList && !active
                width: ListView.view.width
                height: Math.max(64, body.implicitHeight + 16)
                background: Rectangle {
                    color: listRow.active ? window.accentAlpha(0.16) :
                           listRow.down ? Qt.rgba(0.5, 0.5, 0.5, 0.15) :
                           listRow.marked ? Qt.rgba(0.5, 0.5, 0.5, 0.10) :
                           "transparent"
                    Rectangle {
                        visible: listRow.active
                        width: 5
                        height: parent.height
                        color: Material.accent
                    }
                    Rectangle {
                        visible: listRow.marked
                        width: 3
                        height: parent.height
                        color: window.accentAlpha(0.6)
                    }
                }
                contentItem: RowLayout {
                    spacing: 10
                    Equalizer {
                        visible: listRow.active
                        running: listRow.active && player.playing
                        Layout.leftMargin: 4
                    }
                    Column {
                        id: body
                        Layout.fillWidth: true
                        Label {
                            text: name
                            width: parent.width
                            elide: Text.ElideRight
                            font.pixelSize: 16
                            font.bold: listRow.active
                            color: listRow.active ? Material.accent
                                                  : Material.foreground
                        }
                        Label {
                            text: (listRow.active ?
                                   (player.playing ? "Playing  ·  "
                                                   : "Paused  ·  ") : "") +
                                  detail +
                                  (matched >= 0 ? "  ·  " + matched + " matched"
                                                : "")
                            width: parent.width
                            wrapMode: Text.Wrap
                            font.pixelSize: 13
                            opacity: 0.65
                        }
                    }
                    RoundButton {
                        text: "▶"; flat: true; font.pixelSize: 20
                        onClicked: player.playPlaylist(index)
                    }
                }
                onClicked: {
                    window.selectedList = index
                    if (kind === "playlist" || kind === "dynamic") {
                        player.openPlaylist(index)
                        entriesOpen = true
                    }
                    else player.playPlaylist(index)
                }
            }
        }

        ListView {
            visible: tab === 1 && entriesOpen
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            model: entries
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { }
            delegate: TrackRow {
                playingNow: trackId === player.currentId
                // play starts here when nothing is playing
                marked: index === 0 && player.currentId < 0
                heading: title
                sub: artist + (album ? "  ·  " + album : "")
                rating: model.rating
                detail: model.detail
                reserve: player.openDynamic
                onClicked: player.playEntry(index)
            }
        }

        // the play controls: a panel of their own, apart from the lists.  The
        // song is named here only when its row is not
        // on the screen (otherwise the list row, with its stars, says it).
        Pane {
            id: controls
            Layout.fillWidth: true
            Layout.topMargin: 6
            padding: 14
            topPadding: 16
            readonly property bool dark:
                Material.theme === Material.Dark ||
                (Material.theme === Material.System &&
                 Qt.styleHints.colorScheme === Qt.Dark)
            background: Rectangle {
                color: controls.dark ? "#2a2730" : "#efe9f3"
                radius: 22
                // square corners at the bottom
                Rectangle {
                    anchors.fill: parent
                    anchors.topMargin: parent.radius
                    color: parent.color
                }
                // a line along the top edge
                Rectangle {
                    anchors.top: parent.top
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width - 2 * parent.radius; height: 2
                    color: window.accentAlpha(0.5)
                }
            }
            ColumnLayout {
                anchors.fill: parent
                spacing: 0
                Label {
                    Layout.fillWidth: true
                    visible: player.currentId < 0 || window.playingRows === 0
                    text: player.currentId < 0 ? "Nothing playing"
                                               : player.title
                    elide: Text.ElideRight; font.pixelSize: 16; font.bold: true
                }
                Label {
                    Layout.fillWidth: true
                    visible: player.currentId >= 0 && window.playingRows === 0
                    text: player.artist +
                          (player.album ? "  ·  " + player.album : "")
                    elide: Text.ElideRight; font.pixelSize: 13; opacity: 0.65
                }
                Stars {
                    id: stars
                    // otherwise the stars are on the song's row
                    visible: player.currentId >= 0 && window.playingRows === 0
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: player.sectionText !== ""
                    Label {
                        Layout.fillWidth: true
                        text: player.sectionText
                        elide: Text.ElideRight
                        font.pixelSize: 13
                        color: Material.accent
                    }
                    Button {
                        text: "Next section"
                        flat: true
                        onClicked: player.nextSection()
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: text !== "" && tracks.total > 0
                    text: player.message
                    color: window.errorColor
                    wrapMode: Text.Wrap; font.pixelSize: 12
                }
                // what the equalizer is doing, at a glance; the EQ button in
                // the top bar changes it
                Label {
                    Layout.fillWidth: true
                    text: player.eqSummary
                    elide: Text.ElideRight
                    font.pixelSize: 12
                    opacity: 0.7
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: clock(player.position); font.pixelSize: 12 }
                    Slider {
                        id: seekBar
                        Layout.fillWidth: true
                        from: 0; to: Math.max(1, player.duration)
                        value: player.position
                        onMoved: player.seek(value)
                    }
                    Label { text: clock(player.duration); font.pixelSize: 12 }
                }
                RowLayout {
                    Layout.fillWidth: true
                    // the row never makes the panel wider than the screen (it
                    // would push the track length off it)
                    Layout.preferredWidth: 0
                    spacing: 4
                    Item { Layout.fillWidth: true }
                    ShuffleButton {
                        on: player.shuffle
                        available: player.shuffleAvailable
                        onToggled: function(value) { player.shuffle = value }
                    }
                    RoundButton {
                        text: "⏮"
                        font.pixelSize: 22
                        flat: true
                        onClicked: player.previous()
                    }
                    RoundButton {
                        text: player.playing ? "⏸" : "▶"; font.pixelSize: 26
                        Material.background: Material.accent
                        Material.foreground: window.accentInk
                        onClicked: {
                            // on the Playlists tab with nothing playing, play
                            // starts the marked playlist, not the first song of
                            // the library
                            var idle = player.currentId < 0 && tab === 1
                            if (idle && entriesOpen) player.playEntry(0)
                            else if (idle && playlists.count > 0)
                                player.playPlaylist(
                                    Math.min(window.selectedList,
                                             playlists.count - 1))
                            else player.toggle()
                        }
                    }
                    RoundButton {
                        text: "⏭"
                        font.pixelSize: 22
                        flat: true
                        onClicked: player.next()
                    }
                    // as wide as the shuffle button, so the play button stays
                    // centred
                    Item { implicitWidth: 76 }
                    Item { Layout.fillWidth: true }
                }
            }
        }
    }

    Connections {
        target: playlists
        function onCountChanged() {
            window.chooseStartTab()
            if (window.selectedList >= playlists.count) window.selectedList = 0
        }
    }
    function chooseStartTab() {
        if (startTabChosen || playlists.count === 0) return;
        startTabChosen = true;
        tab = 1;
    }
    Component.onCompleted: chooseStartTab()

    Connections {
        target: player
        function onSyncFinished() { syncDialog.close() }
    }

    PopupDialog {
        id: equalizer
        title: "Equalizer"
        standardButtons: Dialog.Close
        ColumnLayout {
            anchors.fill: parent
            spacing: 4
            Switch {
                text: "Equalizer on"
                checked: player.eqOn
                onToggled: player.eqOn = checked
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                font.pixelSize: 12
                opacity: 0.7
                text: "Switching it on or off takes effect with the next song."
            }
            Switch {
                Layout.fillWidth: true
                text: "Auto: a preset for each song's genre"
                checked: player.eqAuto
                onToggled: player.eqAuto = checked
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                font.pixelSize: 12
                opacity: 0.7
                visible: player.eqAuto
                text: player.eqAutoName !== ""
                      ? "This song: " + player.eqAutoName
                      : "This song has no genre with a preset: the settings " +
                        "below are used."
            }
            // while a song's automatic preset applies, the controls show it
            // and are locked: they would change settings that are not used
            // the preset's name between two arrows; each arrow moves to the
            // next preset (round the list) and it applies at once
            RowLayout {
                id: presetRow
                Layout.fillWidth: true
                readonly property bool locked:
                    player.eqAuto && player.eqAutoName !== ""
                function step(direction) {
                    var count = player.eqPresets.length;
                    var at = player.eqPresets.indexOf(player.eqShownPreset);
                    // "Custom" is not in the list: go to the first or last
                    at = at < 0 ? (direction > 0 ? 0 : count - 1)
                                : (at + direction + count) % count;
                    player.setEqPreset(player.eqPresets[at]);
                }
                ToolButton {
                    text: "\u2190"
                    font.pixelSize: 28
                    enabled: !presetRow.locked
                    onClicked: presetRow.step(-1)
                }
                Label {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    font.pixelSize: 18
                    opacity: presetRow.locked ? 0.6 : 1
                    text: player.eqShownPreset
                }
                ToolButton {
                    text: "\u2192"
                    font.pixelSize: 28
                    enabled: !presetRow.locked
                    onClicked: presetRow.step(1)
                }
            }
            Repeater {
                model: 5
                RowLayout {
                    Layout.fillWidth: true
                    TextMetrics {
                        id: bandWidth
                        font: bandText.font
                        text: "3.6 kHz"  // the widest, so the bars line up
                    }
                    Label {
                        id: bandText
                        Layout.minimumWidth: bandWidth.advanceWidth + 2
                        text: player.eqBands[index]
                    }
                    Slider {
                        id: bandSlider
                        Layout.fillWidth: true
                        from: -12
                        to: 12
                        stepSize: 1
                        enabled: !(player.eqAuto && player.eqAutoName !== "")
                        onMoved: player.setEqBand(index, value)
                        // moving the slider by hand replaces a plain binding
                        // for good; this one stays, so the bar follows the
                        // song's automatic preset
                        Binding {
                            target: bandSlider
                            property: "value"
                            value: player.eqShownGains[index]
                            restoreMode: Binding.RestoreNone
                        }
                    }
                    TextMetrics {
                        id: dbWidth
                        font: dbText.font
                        text: "-12 dB"
                    }
                    Label {
                        id: dbText
                        // wide enough for "-12 dB", never cut off; the slider
                        // gives way
                        Layout.minimumWidth: dbWidth.advanceWidth + 2
                        horizontalAlignment: Text.AlignRight
                        text: (player.eqShownGains[index] > 0 ? "+" : "") +
                              player.eqShownGains[index].toFixed(0) + " dB"
                    }
                }
            }
            // saved presets: "Save as" keeps the five sliders under a name;
            // Delete removes the saved preset that is shown
            RowLayout {
                Layout.fillWidth: true
                Button {
                    text: "Save as\u2026"
                    enabled: !presetRow.locked
                    onClicked: {
                        presetName.text = player.eqCustomNames.indexOf(
                            player.eqShownPreset) >= 0
                            ? player.eqShownPreset : ""
                        presetError.text = ""
                        savePreset.open()
                        presetName.forceActiveFocus()
                    }
                }
                Button {
                    text: "Delete"
                    enabled: player.eqCustomNames.indexOf(
                                 player.eqShownPreset) >= 0
                    onClicked: player.deleteEqPreset(player.eqShownPreset)
                }
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                font.pixelSize: 12
                opacity: 0.7
                text: "With Auto on, a saved preset is used for a song when " +
                      "one of its genres contains the preset's name."
            }
        }
    }

    PopupDialog {
        id: savePreset
        title: "Save preset"
        standardButtons: Dialog.Save | Dialog.Cancel
        onAccepted: {
            var why = player.saveEqPreset(presetName.text)
            if (why !== "") {
                presetError.text = why
                open()
            }
        }
        ColumnLayout {
            anchors.fill: parent
            TextField {
                id: presetName
                Layout.fillWidth: true
                placeholderText: "Name, such as a genre"
            }
            Label {
                id: presetError
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Material.accent
                visible: text !== ""
            }
        }
    }

    // The pop-up windows (the three-dot menu and its dialogs): a dimmed page
    // behind, a border in the accent colour and a bold accent title, so that
    // they stand out from the page in both the light and the dark theme.
    component PopupDialog: Dialog {
        readonly property bool night: Qt.styleHints.colorScheme === Qt.Dark
        modal: true
        anchors.centerIn: Overlay.overlay
        width: Overlay.overlay.width - 32
        standardButtons: Dialog.Close
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, night ? 0.75 : 0.6) }
        background: Rectangle {
            radius: 16
            color: night ? "#36323f" : "#ffffff"
            border.width: 3
            border.color: Material.accent
        }
        header: Label {
            text: title
            font.pixelSize: 22
            font.bold: true
            color: Material.accent
            padding: 18
            bottomPadding: 6
        }
    }

    // the three-dot menu: one entry per concern, each with a dialog of its own
    Menu {
        id: mainMenu
        readonly property bool night: Qt.styleHints.colorScheme === Qt.Dark
        modal: true
        dim: true
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, night ? 0.75 : 0.6) }
        background: Rectangle {
            implicitWidth: 260
            radius: 12
            color: mainMenu.night ? "#36323f" : "#ffffff"
            border.width: 3
            border.color: Material.accent
        }
        MenuItem {
            text: "Volume..."; font.pixelSize: 18; height: 52
            onTriggered: volumeDialog.open()
        }
        MenuItem {
            text: "Equalizer..."; font.pixelSize: 18; height: 52
            onTriggered: equalizer.open()
        }
        MenuItem {
            text: "Library..."; font.pixelSize: 18; height: 52
            onTriggered: libraryDialog.open()
        }
        MenuItem {
            text: "Sync with the desktop..."; font.pixelSize: 18; height: 52
            onTriggered: syncDialog.open()
        }
        MenuItem {
            text: "Build " + player.build
            enabled: false; font.pixelSize: 13; height: 36
        }
    }

    PopupDialog {
        id: volumeDialog
        title: "Volume"
        standardButtons: Dialog.Close
        ColumnLayout {
            anchors.fill: parent
            spacing: 8
            Switch {
                text: "Volume leveling"
                checked: player.leveling
                onToggled: player.leveling = checked
            }
            RowLayout {
                Label { text: "Target " + player.target.toFixed(0) + " LUFS" }
                Slider {
                    Layout.fillWidth: true
                    from: -30
                    to: -8
                    stepSize: 1
                    value: player.target
                    onMoved: player.target = value
                }
            }
            RowLayout {
                Label { text: "Volume" }
                Slider {
                    Layout.fillWidth: true
                    from: 0
                    to: 1
                    value: player.volume
                    onMoved: player.volume = value
                }
            }
        }
    }

    PopupDialog {
        id: libraryDialog
        title: "Library"
        standardButtons: Dialog.Close
        ColumnLayout {
            anchors.fill: parent
            spacing: 8
            TextField {
                id: dbField
                Layout.fillWidth: true
                text: player.dbPath
                placeholderText: "database (tags.db)"
            }
            TextField {
                id: rootField
                Layout.fillWidth: true
                text: player.musicRoot
                placeholderText: "music folder"
            }
            Button {
                text: "Load library"
                onClicked: {
                    player.setPaths(dbField.text, rootField.text)
                    libraryDialog.close()
                }
            }
            Label {
                Layout.fillWidth: true
                text: player.message
                wrapMode: Text.Wrap
                font.pixelSize: 12
                opacity: 0.7
            }
        }
    }

    PopupDialog {
        id: syncDialog
        title: "Sync with the desktop"
        closePolicy: player.syncRunning
            ? Popup.NoAutoClose
            : Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onRejected: if (player.syncRunning) player.stopSync()
        ColumnLayout {
            anchors.fill: parent
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: player.syncRunning ? "Waiting for the desktop"
                                             : "Not running"
                    font.bold: true
                }
                Button {
                    text: player.syncRunning ? "Stop" : "Start"
                    onClicked: {
                        if (player.syncRunning) player.stopSync()
                        else player.startSync()
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                visible: player.syncRunning
                Label {
                    Layout.fillWidth: true
                    text: "Code " + player.syncCode
                    font.pixelSize: 24
                    font.bold: true
                    color: Material.accent
                }
                Button {
                    text: "New code"
                    flat: true
                    onClicked: player.newSyncCode()
                }
            }
            Label {
                Layout.fillWidth: true
                visible: player.syncRunning && player.syncAddresses !== ""
                text: "Address: " +
                      player.syncAddresses.replace("\n", ", ") + "  port 8765"
                wrapMode: Text.Wrap; font.pixelSize: 12; opacity: 0.8
            }
            Label {
                Layout.fillWidth: true
                visible: player.syncStatus !== ""
                text: player.syncStatus
                wrapMode: Text.Wrap
                font.pixelSize: 12
            }
        }
    }
}
