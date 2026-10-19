# Copyright (c) 2026 Logic Magicians Software
# SPDX-License-Identifier: GPL-3.0-only
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published
# by the Free Software Foundation.  It is distributed WITHOUT ANY WARRANTY;
# see the file LICENSE for the license text.
#
# Reads and writes the tags of any media format through mutagen.
#
# Every file's tags are presented as ``dict[str, list[str]]`` with upper-case
# keys.  Well-known tags use the same name in every format (TITLE, ARTIST, ...);
# anything else is stored natively as a custom field (a Vorbis comment, an ID3
# TXXX frame, an MP4 free-form atom or an ASF attribute).  Fields that are not
# text (cover art and the like) are neither shown nor touched.
from __future__ import annotations

import os
import re
from collections.abc import Iterator

import mutagen
from mutagen import asf, id3, mp4
from mutagen._vorbis import VComment

MEDIA_EXTENSIONS = frozenset({
    ".mp3",
    ".flac",
    ".ogg",
    ".oga",
    ".opus",
    ".spx",
    ".m4a",
    ".m4b",
    ".mp4",
    ".wma",
    ".wav",
    ".aif",
    ".aiff",
    ".dsf",
})

# Canonical name -> (ID3 frame, MP4 atom, ASF attribute); None = no native slot.
_CANON: dict[str, tuple[str, str | None, str]] = {
    "TITLE": ("TIT2", "\xa9nam", "Title"),
    "SUBTITLE": ("TIT3", None, "WM/SubTitle"),
    "ARTIST": ("TPE1", "\xa9ART", "Author"),
    "ALBUM": ("TALB", "\xa9alb", "WM/AlbumTitle"),
    "ALBUMARTIST": ("TPE2", "aART", "WM/AlbumArtist"),
    "DATE": ("TDRC", "\xa9day", "WM/Year"),
    "GENRE": ("TCON", "\xa9gen", "WM/Genre"),
    "TRACKNUMBER": ("TRCK", "trkn", "WM/TrackNumber"),
    "DISCNUMBER": ("TPOS", "disk", "WM/PartOfSet"),
    "COMPOSER": ("TCOM", "\xa9wrt", "WM/Composer"),
    "CONDUCTOR": ("TPE3", None, "WM/Conductor"),
    "LYRICIST": ("TEXT", None, "WM/Writer"),
    "GROUPING": ("TIT1", "\xa9grp", "WM/ContentGroupDescription"),
    "COMMENT": ("COMM", "\xa9cmt", "Description"),
    "COPYRIGHT": ("TCOP", "cprt", "Copyright"),
    "PUBLISHER": ("TPUB", None, "WM/Publisher"),
    "ISRC": ("TSRC", None, "WM/ISRC"),
    "BPM": ("TBPM", "tmpo", "WM/BeatsPerMinute"),
}
COMMON_KEYS = list(_CANON)

_ID3_FWD = {k: v[0] for k, v in _CANON.items()}
_ID3_REV = {v: k for k, v in _ID3_FWD.items()}
_MP4_FWD = {k: v[1] for k, v in _CANON.items() if v[1]}
_MP4_REV = {v: k for k, v in _MP4_FWD.items()}
_ASF_FWD = {k: v[2] for k, v in _CANON.items()}
_ASF_REV = {v.lower(): k for k, v in _ASF_FWD.items()}

_VORBIS_HIDDEN = {"METADATA_BLOCK_PICTURE", "COVERART", "COVERARTMIME"}
_FREEFORM = "----:com.apple.iTunes:"
_ORDER = {k: i for i, k in enumerate(_CANON)}


class TagError(Exception):
    # A file could not be read or written.
    pass


def sort_key(key: str) -> tuple[int, str]:
    # Order well-known tags first (in a sensible order), the rest
    # alphabetically.
    return (_ORDER.get(key, len(_ORDER)), key)


def find_media(root: str) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort(key=str.lower)
        for name in sorted(filenames, key=str.lower):
            if name.startswith("._"):
                continue  # macOS AppleDouble sidecar, not audio
            if os.path.splitext(name)[1].lower() in MEDIA_EXTENSIONS:
                yield os.path.join(dirpath, name)


def _open(path: str):
    try:
        audio = mutagen.File(path)
    except Exception as e:  # mutagen raises assorted errors on corrupt files
        raise TagError(str(e) or type(e).__name__) from e
    if audio is None:
        raise TagError("unsupported or unreadable file")
    return audio


def _kind(tags) -> str:
    if isinstance(tags, id3.ID3):
        return "id3"
    if isinstance(tags, mp4.MP4Tags):
        return "mp4"
    if isinstance(tags, asf.ASFTags):
        return "asf"
    if isinstance(tags, VComment):
        return "vorbis"
    raise TagError(f"unsupported tag format ({type(tags).__name__})")


# --------------------------------------------------------------------- reading


def _read_vorbis(tags) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key in tags.keys():
        if key.upper() not in _VORBIS_HIDDEN:
            out.setdefault(key.upper(), []).extend(tags[key])
    return out


def _read_id3(tags) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for frame in tags.values():
        fid = frame.FrameID
        if fid == "TXXX":
            key = frame.desc.upper()
        elif fid == "COMM":
            if frame.desc:
                continue
            key = "COMMENT"
        elif isinstance(frame, id3.TextFrame):
            key = _ID3_REV.get(fid, "ID3:" + fid)
        else:
            continue
        if key:
            out.setdefault(key, []).extend(str(t) for t in frame.text)
    return out


def _read_mp4(tags) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for atom, values in tags.items():
        if not isinstance(values, list):
            continue  # flags such as cpil/pgap are plain bools
        if atom in ("trkn", "disk"):
            vals = [f"{n}/{t}" if t else str(n) for n, t in values]
        elif atom == "tmpo":
            vals = [str(v) for v in values]
        elif atom.startswith("----:"):
            vals = [bytes(v).decode("utf-8", "replace") for v in values]
        elif all(isinstance(v, str) for v in values):
            vals = list(values)
        else:
            continue  # cover art, flags, ...
        if atom.startswith("----:"):
            key = atom.split(":", 2)[2].upper()
        else:
            key = _MP4_REV.get(atom, "MP4:" + atom)
        if key:
            out.setdefault(key, []).extend(vals)
    return out


def _read_asf(tags) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, attr in tags.items():
        if isinstance(attr, asf.ASFByteArrayAttribute):
            continue
        out.setdefault(_ASF_REV.get(name.lower(), name.upper()), []).append(
            str(attr.value)
        )
    return out


_READERS = {
    "vorbis": _read_vorbis,
    "id3": _read_id3,
    "mp4": _read_mp4,
    "asf": _read_asf,
}


def read_tags(path: str) -> dict[str, list[str]]:
    tags = _open(path).tags
    try:
        return {} if tags is None else _READERS[_kind(tags)](tags)
    except TagError:
        raise
    except Exception as e:  # odd tag content must not take the whole scan down
        raise TagError(f"could not parse tags: {e}") from e


# --------------------------------------------------------------------- writing


def _id3_frame_id(key: str) -> str | None:
    return key[4:] if key.startswith("ID3:") else _ID3_FWD.get(key)


def _id3_delete(tags, key: str) -> None:
    fid = _id3_frame_id(key)
    for hash_key, frame in list(tags.items()):
        if frame.FrameID == "TXXX" and frame.desc.upper() == key:
            del tags[hash_key]
        elif fid == "COMM" and frame.FrameID == "COMM" and not frame.desc:
            del tags[hash_key]
    if fid and fid != "COMM":
        tags.delall(fid)


def _id3_set(tags, key: str, values: list[str]) -> None:
    fid = _id3_frame_id(key)
    if fid and fid != "COMM" and fid not in id3.Frames:
        raise TagError(f"unknown ID3 frame {fid}")
    _id3_delete(tags, key)
    if fid == "COMM":
        tags.add(id3.COMM(encoding=3, lang="eng", desc="", text=values))
    elif fid:
        tags.add(id3.Frames[fid](encoding=3, text=values))
    else:
        tags.add(id3.TXXX(encoding=3, desc=key, text=values))


def _mp4_atom(key: str) -> str | None:
    return key[4:] if key.startswith("MP4:") else _MP4_FWD.get(key)


def _mp4_delete(tags, key: str) -> None:
    atom = _mp4_atom(key)
    for name in list(tags.keys()):
        if name == atom or (
            name.startswith("----:") and name.split(":", 2)[2].upper() == key
        ):
            del tags[name]


_NUM_OF_TOTAL = re.compile(r"\s*(\d+)\s*(?:/\s*(\d+))?\s*")


def _mp4_set(tags, key: str, values: list[str]) -> None:
    atom = _mp4_atom(key)
    if atom in ("trkn", "disk"):
        new: list = []
        for v in values:
            m = _NUM_OF_TOTAL.fullmatch(v)
            if not m:
                raise TagError(f"{key} must look like 3 or 3/12, not {v!r}")
            new.append((int(m[1]), int(m[2] or 0)))
    elif atom == "tmpo":
        try:
            new = [int(v) for v in values]
        except ValueError as e:
            raise TagError(f"{key} must be a whole number") from e
    elif atom:
        new = list(values)
    else:
        atom = _FREEFORM + key
        new = [mp4.MP4FreeForm(v.encode("utf-8")) for v in values]
    _mp4_delete(tags, key)
    tags[atom] = new


def _asf_names(tags, key: str) -> set[str]:
    native = _ASF_FWD.get(key, key).lower()
    return {
        n for n, _ in tags.items() if n.lower() == native or n.upper() == key
    }


def _asf_delete(tags, key: str) -> None:
    for name in _asf_names(tags, key):
        del tags[name]


def _asf_set(tags, key: str, values: list[str]) -> None:
    _asf_delete(tags, key)
    tags[_ASF_FWD.get(key, key)] = [asf.ASFUnicodeAttribute(v) for v in values]


def _vorbis_delete(tags, key: str) -> None:
    if key in tags:
        del tags[key]


def _vorbis_set(tags, key: str, values: list[str]) -> None:
    try:
        tags[key] = list(values)
    except ValueError as e:
        raise TagError(f"invalid tag name {key!r}: {e}") from e


_WRITERS = {
    "vorbis": (_vorbis_set, _vorbis_delete),
    "id3": (_id3_set, _id3_delete),
    "mp4": (_mp4_set, _mp4_delete),
    "asf": (_asf_set, _asf_delete),
}


def write_tags(
    path: str, changes: dict[str, list[str] | None]
) -> dict[str, list[str]]:
    # Apply ``changes`` (key -> new values, or None to remove) and save.
    #
    # Only the listed keys are touched.  Returns the file's tags as re-read
    # after saving.
    audio = _open(path)
    try:
        if audio.tags is None:
            audio.add_tags()
        setter, deleter = _WRITERS[_kind(audio.tags)]
        for key, values in changes.items():
            if values:
                setter(audio.tags, key, values)
            else:
                deleter(audio.tags, key)
        audio.save()
    except TagError:
        raise
    except Exception as e:
        raise TagError(str(e) or type(e).__name__) from e
    return read_tags(path)
