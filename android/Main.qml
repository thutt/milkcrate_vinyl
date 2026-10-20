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
    Material.accent: Material.Pink
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
        signal toggled(bool value)
        implicitWidth: 76; implicitHeight: 68
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
                property color ink: sh.on ? "white"
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
            text: sh.on ? "SHUFFLE ON" : "shuffle off"
            font.pixelSize: 11
            font.bold: sh.on
            font.letterSpacing: sh.on ? 0.8 : 0
            color: sh.on ? Material.accent : window.foregroundAlpha(0.6)
        }
        MouseArea { anchors.fill: parent; onClicked: sh.toggled(!sh.on) }
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
                color: "white"
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
                       Material.color(Material.Amber) : Material.foreground
                opacity: index < player.rating ? 1 : 0.6
                padding: 3
                MouseArea {
                    anchors.fill: parent
                    onClicked: player.rate(index + 1)
                }
            }
        }
    }

    // Three bars that bounce while music plays and stand still when paused.
    component Equalizer: Item {
        id: eq
        property bool running: false
        Layout.preferredWidth: 22; Layout.preferredHeight: 22
        Row {
            anchors.bottom: parent.bottom
            spacing: 3
            Repeater {
                model: 3
                Rectangle {
                    id: bar
                    width: 5; radius: 1
                    color: Material.accent
                    anchors.bottom: parent.bottom
                    height: 8
                    SequentialAnimation on height {
                        running: eq.running
                        loops: Animation.Infinite
                        onRunningChanged: {
                            if (!running) bar.height = 8 + index * 4
                        }
                        NumberAnimation {
                            to: 20 - index * 4
                            duration: 320 + index * 110
                            easing.type: Easing.InOutSine
                        }
                        NumberAnimation {
                            to: 6 + index * 3
                            duration: 380 - index * 60
                            easing.type: Easing.InOutSine
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
        width: ListView.view.width
        height: playingNow ? 104 : 64
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
                Stars { visible: row.playingNow }
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
            ToolButton {
                text: "⋮"
                font.pixelSize: 22
                onClicked: settings.open()
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
                    color: Material.color(Material.Red)
                    wrapMode: Text.Wrap; font.pixelSize: 12
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
                        Material.foreground: "white"
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
        function onSyncFinished() { settings.close() }
    }

    Dialog {
        id: equalizer
        title: "Equalizer"
        modal: true
        anchors.centerIn: Overlay.overlay
        width: window.width - 32
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
                text: "Automatic: a preset for each song's genre"
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
            ComboBox {
                Layout.fillWidth: true
                model: player.eqPresets
                displayText: player.eqPreset
                currentIndex: player.eqPresets.indexOf(player.eqPreset)
                onActivated: player.setEqPreset(currentText)
            }
            Repeater {
                model: 5
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.preferredWidth: 64
                        text: player.eqBands[index]
                    }
                    Slider {
                        Layout.fillWidth: true
                        from: -12
                        to: 12
                        stepSize: 1
                        value: player.eqGains[index]
                        onMoved: player.setEqBand(index, value)
                    }
                    Label {
                        Layout.preferredWidth: 56
                        horizontalAlignment: Text.AlignRight
                        text: (player.eqGains[index] > 0 ? "+" : "") +
                              player.eqGains[index].toFixed(0) + " dB"
                    }
                }
            }
        }
    }

    Dialog {
        id: settings
        title: "Settings"
        modal: true
        anchors.centerIn: Overlay.overlay
        width: window.width - 32
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
            Button {
                text: "Equalizer..."
                onClicked: {
                    settings.close()
                    equalizer.open()
                }
            }
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
                    settings.close()
                }
            }
            ToolSeparator { Layout.fillWidth: true; orientation: Qt.Horizontal }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: "Sync with the desktop"
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
            Label {
                Layout.fillWidth: true
                text: player.message
                wrapMode: Text.Wrap
                font.pixelSize: 12
                opacity: 0.7
            }
        }
    }
}
