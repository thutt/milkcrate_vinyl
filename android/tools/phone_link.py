# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# The desktop end of the Wi-Fi sync protocol with the phone app.
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import threading
import urllib.parse

PORT = 8765
DISCOVERY_PORT = 8766
CONNECT = 10  # seconds to wait for the phone to accept a connection


class LinkError(RuntimeError):
    pass


class WrongCode(LinkError):
    # The phone refused the pairing code.
    pass


class NoDatabase(LinkError):
    # The phone has no tags.db yet (a new phone).
    pass


class Conflict(LinkError):
    # The phone wrote to its database after the copy was taken.
    pass


def candidate_hosts() -> list[str]:
    # Addresses worth trying: the gateways of the network links (with USB
    # tethering the phone is the gateway).
    hosts: list[str] = []
    out = subprocess.run(
        ["ip", "-4", "route", "show"], capture_output=True, text=True
    ).stdout
    for line in out.splitlines():
        parts = line.split()
        if "via" in parts:
            hosts.append(parts[parts.index("via") + 1])
    return list(dict.fromkeys(hosts))


def reachable(host: str, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, PORT), timeout=timeout):
            return True
    except OSError:
        return False


class Link:
    def __init__(self, host: str, code: str):
        self.host, self.code = host, code
        self.info: dict = {}
        self.aborted = False
        self.lock = threading.Lock()
        self.sockets: list[socket.socket] = []  # in use, for abort()

    def abort(self) -> None:
        # Make every request, running or later, fail at once (a phone that
        # went to sleep would otherwise keep a request waiting for its
        # timeout).
        with self.lock:
            self.aborted = True
            sockets = list(self.sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self.host, PORT, timeout=CONNECT)

    def request(
        self,
        method: str,
        path: str,
        body=None,
        length: int | None = None,
        headers: dict | None = None,
    ):
        # Returns (status, response headers, response body).
        h = {"X-Pin": self.code, **(headers or {})}
        if length is not None:
            h["Content-Length"] = str(length)
        conn = self._connection()
        try:
            conn.connect()
            conn.sock.settimeout(60)
            with self.lock:
                if self.aborted:
                    raise OSError("stopped")
                self.sockets.append(conn.sock)
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            data = r.read()
            return r.status, dict(r.getheaders()), data
        except (OSError, http.client.HTTPException) as e:
            raise LinkError(f"{method} {path}: {e}") from e
        finally:
            with self.lock:
                if conn.sock in self.sockets:
                    self.sockets.remove(conn.sock)
            conn.close()

    def _ok(self, method, path, **kw):
        status, headers, data = self.request(method, path, **kw)
        if status == 403:
            raise WrongCode(f"{method} {path}: {data.decode(errors='replace')}")
        if status == 409:
            raise Conflict(data.decode(errors="replace"))
        if status != 200:
            raise LinkError(
                f"{method} {path}: {status} {data.decode(errors='replace')}"
            )
        return headers, data

    def hello(self) -> dict:
        return json.loads(self._ok("GET", "/hello")[1])

    def get_database(self, target: str) -> int:
        # Save the phone's database as target; returns its generation.
        status, headers, data = self.request("GET", "/db")
        if status == 404:
            raise NoDatabase("the phone has no database yet")
        if status != 200:
            raise LinkError(
                f"GET /db: {status} {data.decode(errors='replace')}"
            )
        with open(target, "wb") as f:
            f.write(data)
        return int(headers.get("X-Generation", "0"))

    def put_database(self, source: str, generation: int) -> None:
        with open(source, "rb") as f:
            self._ok(
                "PUT",
                "/db",
                body=f,
                length=os.path.getsize(source),
                headers={"X-Generation": str(generation)},
            )

    def files(self) -> dict[str, int]:
        return {
            k: int(v)
            for k, v in json.loads(self._ok("GET", "/files")[1]).items()
        }

    def put_file(self, source: str, rel: str) -> None:
        with open(source, "rb") as f:
            self._ok(
                "PUT",
                "/file/" + urllib.parse.quote(rel, safe="/"),
                body=f,
                length=os.path.getsize(source),
            )

    def done(self) -> None:
        self._ok("POST", "/done", body=b"", length=0)


def broadcast_addresses() -> list[str]:
    out = subprocess.run(
        ["ip", "-4", "-o", "addr"], capture_output=True, text=True
    ).stdout
    found = ["255.255.255.255"]
    for line in out.splitlines():
        parts = line.split()
        if "brd" in parts:
            found.append(parts[parts.index("brd") + 1])
    return list(dict.fromkeys(found))


def discover(wait: float = 2.0) -> list[str]:
    # Phones with the sync switched on answer a broadcast on the local networks;
    # returns their addresses.
    import time

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.3)
    found: list[str] = []
    end = time.time() + wait
    last = 0.0
    while time.time() < end:
        if (
            time.time() - last > 0.7
        ):  # ask again now and then: a datagram can be lost
            last = time.time()
            for addr in broadcast_addresses():
                try:
                    sock.sendto(b"MUSICPLAYER-SYNC?", (addr, DISCOVERY_PORT))
                except OSError:
                    pass
        try:
            data, (host, _port) = sock.recvfrom(256)
        except OSError:
            continue
        if data.startswith(b"MUSICPLAYER-SYNC") and host not in found:
            found.append(host)
    sock.close()
    return found


RECENT_CODES = 5


def remember(recent: list[dict], code: str, name: str = "") -> list[dict]:
    # The codes used lately, newest first, without repeats, at most
    # RECENT_CODES: [{"code": ..., "name": ...}].
    old = next((r for r in recent if r.get("code") == code), {})
    entry = {"code": code, "name": name or old.get("name", "")}
    return ([entry] + [r for r in recent if r.get("code") != code])[
        :RECENT_CODES
    ]


def find_phone(code: str, host: str | None = None) -> Link:
    # The phone with this pairing code: at host, else among those that answer a
    # search of the local network (with
    # several phones listening, the code says which one), else at a gateway (USB
    # tethering).  link.info is its hello.
    tried: list[str] = []
    refused: list[str] = []
    for h in ([host] if host else []) + (
        [] if host else discover() + candidate_hosts()
    ):
        if h in tried:
            continue
        tried.append(h)
        if not reachable(h):
            continue
        link = Link(h, code)
        try:
            link.info = link.hello()
            return link
        except WrongCode:
            refused.append(h)  # another phone, or an old code
        except LinkError:
            pass
    if refused:
        raise WrongCode(
            "The phone at "
            + ", ".join(refused)
            + " refused the pairing code.  Check the code in the app, Settings,"
            ' Sync with the desktop; it changes with "New code".'
        )
    raise LinkError(
        "The phone's sync server was not found"
        + (
            f" at {host}"
            if host
            else f" (tried: {', '.join(tried) or 'nothing answered'})"
        )
        + ".  On the phone: open the app, Settings, Sync with the desktop,"
        " Start; the phone and this computer must be on the same Wi-Fi."
    )
