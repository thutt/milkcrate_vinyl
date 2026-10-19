// Copyright (c) 2026 Logic Magicians Software
// SPDX-License-Identifier: GPL-3.0-only
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License version 3 as published
// by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
// see the file LICENSE for the license text.
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickStyle>

#include "player.h"

int main(int argc, char *argv[]) {
    QGuiApplication app(argc, argv);
    app.setOrganizationName("musicplayer");
    app.setApplicationName("player");
    QQuickStyle::setStyle("Material");
    Player player;
    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty("player", &player);
    engine.rootContext()->setContextProperty("tracks", player.model());
    engine.rootContext()->setContextProperty("playlists",
                                             player.playlistModel());
    engine.rootContext()->setContextProperty("entries", player.entryModel());
    QObject::connect(
        &engine, &QQmlApplicationEngine::objectCreationFailed, &app,
        [] { QCoreApplication::exit(1); }, Qt::QueuedConnection);
    engine.loadFromModule("MusicPlayer", "Main");
    player.start();
    return app.exec();
}
