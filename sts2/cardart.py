"""Card art read out of the game's own Godot archive.

This mirrors what localize.py does with card text: the installed game is read
from and never written to, nothing is downloaded, and nothing leaves the
machine. The art belongs to Mega Crit, so the extracted PNGs go to the user's
state directory and are never bundled with the app or committed.

The pipeline has three steps:

  1. Walk the .pck index (same layout localize.py already reads) for the
     AtlasTexture stubs under images/atlases/card_atlas.sprites/. Each is a
     small text resource naming an atlas and a Rect2 region.
  2. Decode the atlases. They are stored as Godot .ctex with BPTC (BC7) VRAM
     compression — a 52-byte header then raw BC7 blocks, no mipmaps.
  3. Crop each region and write <card-id>.png, plus a manifest the app reads
     at startup so templates never link an image that is not there.

Coverage is bounded by the installed game: a card added to the wiki after the
build you have on disk simply has no art in the archive, and the manifest
records it as missing so the tile falls back to text.
"""
from __future__ import annotations

import json
import logging
import re
import struct
import time
from pathlib import Path

from .config import DATA_DIR, GAME_INSTALL_DIR, STATE_DIR

log = logging.getLogger(__name__)

SPRITE_PREFIX = "images/atlases/card_atlas.sprites/"
ART_DIRNAME = "cardart"
MANIFEST = "manifest.json"

# Godot Image::Format values we can handle.
_FORMAT_BPTC_RGBA = 22
_DATA_FORMAT_IMAGE = 0

_RE_ATLAS = re.compile(r'path="res://([^"]+card_atlas_\d+\.png)"')
_RE_REGION = re.compile(
    r"region\s*=\s*Rect2\(\s*([\d.\-]+),\s*([\d.\-]+),\s*([\d.\-]+),\s*([\d.\-]+)\s*\)")
_RE_IMPORTED = re.compile(r"\.godot/imported/(card_atlas_\d+)\.png-\w+\.bptc\.ctex$")


class ArtError(RuntimeError):
    """The game archive could not be read, or the tools to decode it are absent."""


def art_dir() -> Path:
    return STATE_DIR / ART_DIRNAME


# ── reading the archive ────────────────────────────────────────────────────

def _read_index(f) -> dict[str, tuple[int, int]]:
    """{path: (absolute offset, size)} for every entry in a Godot .pck.

    Encrypted entries are skipped rather than guessed at; this archive has
    none today, but the flag exists and a wrong read would be worse than a
    missing card.
    """
    if f.read(4) != b"GDPC":
        raise ArtError("not a Godot archive")
    f.read(4)                                        # pack format version
    f.read(12)                                       # engine version
    flags, file_base = struct.unpack("<IQ", f.read(12))
    dir_offset, = struct.unpack("<Q", f.read(8))
    f.seek(dir_offset)
    count, = struct.unpack("<I", f.read(4))
    index: dict[str, tuple[int, int]] = {}
    for _ in range(count):
        plen, = struct.unpack("<I", f.read(4))
        path = f.read(plen).rstrip(b"\x00").decode("utf-8", "replace")
        offset, size = struct.unpack("<2Q", f.read(16))
        f.read(16)                                   # md5
        entry_flags, = struct.unpack("<I", f.read(4))
        if entry_flags & 1:                          # encrypted: never touch
            continue
        index[path] = (file_base + offset if flags & 2 else offset, size)
    return index


def _read(f, index, path: str) -> bytes:
    offset, size = index[path]
    f.seek(offset)
    return f.read(size)


def _decode_atlas(blob: bytes):
    """Godot .ctex (GST2, BPTC) -> RGBA Image.

    Header is 52 bytes: magic, version, logical w/h, flags, mipmap limit,
    three reserved words, then data_format, the stored w/h, the mipmap count
    and the image format. Stored height is padded up to a 4px block multiple
    (4070 becomes 4072), so the decode runs at block size and crops back.
    """
    try:
        import texture2ddecoder
        from PIL import Image
    except ImportError as exc:                       # pragma: no cover - env dependent
        raise ArtError(
            "Card art needs Pillow and texture2ddecoder:\n"
            "    pip install pillow texture2ddecoder") from exc

    if blob[:4] != b"GST2":
        raise ArtError(f"not a Godot texture (magic {blob[:4]!r})")
    logical_w, logical_h = struct.unpack("<II", blob[8:16])
    data_format, stored_w, stored_h, mipmaps, fmt = struct.unpack("<IHHII", blob[36:52])
    if data_format != _DATA_FORMAT_IMAGE:
        raise ArtError(f"atlas is stored as data_format {data_format}, expected raw")
    if fmt != _FORMAT_BPTC_RGBA:
        raise ArtError(f"atlas is image format {fmt}, expected BPTC_RGBA")

    blocks_w, blocks_h = (stored_w + 3) // 4, (stored_h + 3) // 4
    payload = blob[52:52 + blocks_w * blocks_h * 16]
    raw = texture2ddecoder.decode_bc7(payload, blocks_w * 4, blocks_h * 4)
    img = Image.frombytes("RGBA", (blocks_w * 4, blocks_h * 4), raw, "raw", "BGRA")
    return img.crop((0, 0, logical_w, logical_h))


# ── mapping cards to regions ───────────────────────────────────────────────

def _regions(f, index) -> dict[str, tuple[str, tuple[int, int, int, int]]]:
    """{sprite path: (atlas res:// path, (x, y, w, h))}."""
    out = {}
    for path in index:
        if not (path.startswith(SPRITE_PREFIX) and path.endswith(".tres")):
            continue
        text = _read(f, index, path).decode("utf-8", "replace")
        atlas, region = _RE_ATLAS.search(text), _RE_REGION.search(text)
        if atlas and region:
            slug = path[len(SPRITE_PREFIX):-len(".tres")]
            out[slug] = (atlas.group(1), tuple(int(float(g)) for g in region.groups()))
    return out


def _by_leaf(regions) -> dict[str, str]:
    """Leaf card name -> best sprite path.

    A card usually appears twice, once under a beta/ folder and once not. The
    live art is the non-beta one, so it wins; a beta-only card keeps its beta
    path rather than being dropped.
    """
    best: dict[str, str] = {}
    for slug in regions:
        leaf = slug.rsplit("/", 1)[-1]
        current = best.get(leaf)
        if current is None or ("/beta/" in current and "/beta/" not in slug):
            best[leaf] = slug
    return best


def _card_ids() -> list[dict]:
    cards = json.loads((DATA_DIR / "cards.json").read_text(encoding="utf-8"))
    if isinstance(cards, dict):
        cards = cards.get("cards", list(cards.values()))
    return cards


def _resolve(cards, leaves) -> tuple[dict[str, str], list[str]]:
    """(card id -> sprite path, ids with no art)."""
    found, missing = {}, []
    for card in cards:
        cid = str(card.get("id", ""))
        if not cid:
            continue
        key = cid.split(".", 1)[1].lower() if "." in cid else cid.lower()
        char = str(card.get("character", "")).lower()
        # Basics are id'd per character (STRIKE_IRONCLAD) but filed under the
        # character's folder as a bare name (ironclad/strike).
        for candidate in (key, key.replace(f"_{char}", "")):
            if candidate in leaves:
                found[cid] = leaves[candidate]
                break
        else:
            missing.append(cid)
    return found, missing


def _filename(card_id: str) -> str:
    stem = card_id.split(".", 1)[1] if "." in card_id else card_id
    return re.sub(r"[^a-z0-9_]+", "_", stem.lower()) + ".png"


def _game_version(game_dir: Path) -> str:
    try:
        info = json.loads((game_dir / "release_info.json").read_text(encoding="utf-8"))
        return str(info.get("version", "unknown"))
    except (OSError, ValueError):
        return "unknown"


# ── the extraction itself ──────────────────────────────────────────────────

def extract(game_dir=None, out_dir=None, on_progress=None) -> dict:
    """Write one PNG per card plus a manifest. Returns a summary dict."""
    started = time.time()
    game_dir = Path(game_dir or GAME_INSTALL_DIR or "")
    pck = game_dir / "SlayTheSpire2.pck"
    if not pck.exists():
        raise ArtError(
            f"No SlayTheSpire2.pck under {game_dir or '(no game directory found)'}. "
            "Point STS2_GAME_DIR at the game's install directory.")

    out = Path(out_dir or art_dir())
    out.mkdir(parents=True, exist_ok=True)

    with open(pck, "rb") as f:
        index = _read_index(f)
        regions = _regions(f, index)
        if not regions:
            raise ArtError(f"No card atlas regions found in {pck}")
        leaves = _by_leaf(regions)
        resolved, missing = _resolve(_card_ids(), leaves)

        ctex_for = {}
        for path in index:
            m = _RE_IMPORTED.match(path)
            if m:
                ctex_for[f"images/atlases/{m.group(1)}.png"] = path

        # Decode each atlas once, writing every card that lives on it before
        # moving on — three 16MB RGBA surfaces at once is not worth holding.
        by_atlas: dict[str, list[tuple[str, str]]] = {}
        for cid, slug in resolved.items():
            by_atlas.setdefault(regions[slug][0], []).append((cid, slug))

        written, skipped = 0, 0
        for atlas_png, members in sorted(by_atlas.items()):
            if atlas_png not in ctex_for:
                log.warning("No imported texture for %s; skipping %d cards",
                            atlas_png, len(members))
                skipped += len(members)
                continue
            if on_progress:
                on_progress(f"decoding {atlas_png} ({len(members)} cards)")
            sheet = _decode_atlas(_read(f, index, ctex_for[atlas_png]))
            for cid, slug in members:
                x, y, w, h = regions[slug][1]
                sheet.crop((x, y, x + w, y + h)).save(out / _filename(cid))
                written += 1
            sheet.close()

    manifest = {
        "game_version": _game_version(game_dir),
        "generated": int(time.time()),
        "cards": {cid: _filename(cid) for cid in sorted(resolved)},
        "missing": sorted(missing),
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    return {
        "written": written,
        "skipped": skipped,
        "missing": len(missing),
        "total": len(resolved) + len(missing),
        "game_version": manifest["game_version"],
        "out_dir": out,
        "seconds": round(time.time() - started, 1),
    }


# ── what the app reads ─────────────────────────────────────────────────────

def load_manifest(out_dir=None) -> dict[str, str]:
    """{card id: filename} for art already on disk, or {} when none is."""
    path = Path(out_dir or art_dir()) / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cards = data.get("cards", {})
    return cards if isinstance(cards, dict) else {}
