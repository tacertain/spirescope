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

import io
import json
import logging
import math
import re
import struct
import time
from pathlib import Path

from .config import DATA_DIR, GAME_INSTALL_DIR, STATE_DIR

log = logging.getLogger(__name__)

SPRITE_PREFIX = "images/atlases/card_atlas.sprites/"
ART_DIRNAME = "cardart"
MANIFEST = "manifest.json"

# Godot Image::Format values we can handle. The card and ui atlases are BC7;
# the compressed_ atlas holding the ancient card template is BC3. Both are
# 16-byte blocks over 4x4 pixels, so only the decoder call differs.
_FORMAT_DXT5 = 19
_FORMAT_BPTC_RGBA = 22
# How the pixels are stored, as opposed to how they are compressed. Atlases are
# raw blocks; the small standalone UI textures are lossless PNG or WebP, which
# Pillow reads on its own.
_DATA_FORMAT_IMAGE = 0
_DATA_FORMAT_PNG = 1
_DATA_FORMAT_WEBP = 2

# The frame furniture, keyed by the filename we write. These are the pieces
# card.tscn layers around the portrait; everything else on a card is text.
UI_SPRITES = {
    # frame, chosen by card type
    "frame_attack": "images/atlases/ui_atlas.sprites/card/card_frame_attack_s.tres",
    "frame_skill": "images/atlases/ui_atlas.sprites/card/card_frame_skill_s.tres",
    "frame_power": "images/atlases/ui_atlas.sprites/card/card_frame_power_s.tres",
    "frame_quest": "images/atlases/ui_atlas.sprites/card/card_frame_quest_s.tres",
    # the ring around the portrait, also per type
    "portrait_attack": "images/atlases/ui_atlas.sprites/card/card_portrait_border_attack_s.tres",
    "portrait_skill": "images/atlases/ui_atlas.sprites/card/card_portrait_border_skill_s.tres",
    "portrait_power": "images/atlases/ui_atlas.sprites/card/card_portrait_border_power_s.tres",
    # name banner and the little type plaque under the portrait
    "banner": "images/atlases/ui_atlas.sprites/card/card_banner.tres",
    "banner_ancient": "images/atlases/ui_atlas.sprites/card/ancient_banner.tres",
    # Ancient cards are a different layout, not a recoloured one: the art bleeds
    # to the card edge and there is no frame or portrait ring at all. These are
    # the AncientBorder and AncientTextBg nodes card.tscn keeps hidden until a
    # card turns out to be Ancient. They live on the compressed_ atlas.
    "ancient_border":
        "images/atlases/compressed.sprites/card_template/ancient_card_border.tres",
    "ancient_text_bg_attack":
        "images/atlases/compressed.sprites/card_template/ancient_card_text_bg_attack.tres",
    "ancient_text_bg_skill":
        "images/atlases/compressed.sprites/card_template/ancient_card_text_bg_skill.tres",
    "ancient_text_bg_power":
        "images/atlases/compressed.sprites/card_template/ancient_card_text_bg_power.tres",
    # cost orb, per character
    "energy_ironclad": "images/atlases/ui_atlas.sprites/card/energy_ironclad.tres",
    "energy_silent": "images/atlases/ui_atlas.sprites/card/energy_silent.tres",
    "energy_defect": "images/atlases/ui_atlas.sprites/card/energy_defect.tres",
    "energy_necrobinder": "images/atlases/ui_atlas.sprites/card/energy_necrobinder.tres",
    "energy_regent": "images/atlases/ui_atlas.sprites/card/energy_regent.tres",
    "energy_colorless": "images/atlases/ui_atlas.sprites/card/energy_colorless.tres",
    "energy_quest": "images/atlases/ui_atlas.sprites/card/energy_quest.tres",
    "unplayable": "images/atlases/ui_atlas.sprites/card/card_unplayable_icon.tres",
}

# Sprites that are a whole texture rather than a region of an atlas, keyed by
# basename because that is all Godot's import step keeps (see
# _imported_textures). The type plaque is one: card.tscn points at
# card_portrait_border_plaque2.png, *not* the card_portrait_border_plaque_s
# region in the ui atlas, and the two are not the same artwork.
UI_TEXTURES = {
    "plaque": "card_portrait_border_plaque2.png",
    # The Regent's star-cost orb. Not on the ui atlas at all: card.tscn's
    # StarIcon points straight at res://images/ui/combat/energy_star.png, the
    # same 256px texture the combat star counter draws.
    "star": "energy_star.png",
}

UI_DIRNAME = "ui"

# hsv.gdshader's (h, s, v) per material, read out of materials/cards/. The
# frame sprite ships red and the banner/border sprites ship teal, so the two
# tables start from different bases and are not interchangeable.
FRAME_TINTS = {
    "red": (0.025, 0.85, 1.0),
    "green": (0.32, 0.45, 1.2),
    "blue": (0.55, 0.9, 1.0),
    "orange": (0.12, 1.5, 1.2),
    "pink": (0.965, 0.55, 1.2),
    "colorless": (1.0, 0.0, 1.2),
    "curse": (0.85, 0.05, 0.55),
    "quest": (1.0, 1.0, 1.0),
}
BANNER_TINTS = {
    "common": (1.0, 0.0, 0.85),
    "uncommon": (1.0, 1.0, 1.0),
    "rare": (0.563, 1.198, 1.14),
    "ancient": (0.0, 0.2, 0.9),
    "curse": (0.27, 1.1, 0.9),
    "event": (0.875, 0.85, 0.9),
    "quest": (0.515, 1.727, 0.9),
    "status": (0.634, 0.35, 0.8),
}

# RGB -> YIQ, exactly the matrix in hsv.gdshader (written there as GLSL
# columns; these are the equivalent rows).
_RGB_TO_YIQ = (
    (0.2989, 0.5870, 0.1140),
    (0.5959, -0.2774, -0.3216),
    (0.2115, -0.5229, 0.3114),
)

_RE_ATLAS = re.compile(r'path="res://([^"]+card_atlas_\d+\.png)"')
_RE_REGION = re.compile(
    r"region\s*=\s*Rect2\(\s*([\d.\-]+),\s*([\d.\-]+),\s*([\d.\-]+),\s*([\d.\-]+)\s*\)")
_RE_MARGIN = re.compile(
    r"margin\s*=\s*Rect2\(\s*([\d.\-]+),\s*([\d.\-]+),\s*([\d.\-]+),\s*([\d.\-]+)\s*\)")
_RE_IMPORTED = re.compile(r"\.godot/imported/(.+?)\.png-[0-9a-f]+(\.\w+)?\.ctex$")


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
    if data_format in (_DATA_FORMAT_PNG, _DATA_FORMAT_WEBP):
        # One length-prefixed payload per mipmap. There are no mipmaps on these
        # and the first is the full image, so the first is all we read.
        size, = struct.unpack("<I", blob[52:56])
        return Image.open(io.BytesIO(blob[56:56 + size])).convert("RGBA")
    if data_format != _DATA_FORMAT_IMAGE:
        raise ArtError(f"atlas is stored as data_format {data_format}, expected raw")
    decoders = {
        _FORMAT_BPTC_RGBA: texture2ddecoder.decode_bc7,
        _FORMAT_DXT5: texture2ddecoder.decode_bc3,
    }
    if fmt not in decoders:
        raise ArtError(f"atlas is image format {fmt}, expected one of {sorted(decoders)}")

    blocks_w, blocks_h = (stored_w + 3) // 4, (stored_h + 3) // 4
    payload = blob[52:52 + blocks_w * blocks_h * 16]
    raw = decoders[fmt](payload, blocks_w * 4, blocks_h * 4)
    img = Image.frombytes("RGBA", (blocks_w * 4, blocks_h * 4), raw, "raw", "BGRA")
    return img.crop((0, 0, logical_w, logical_h))


# ── mapping cards to regions ───────────────────────────────────────────────

def _margin_of(text: str) -> tuple[int, int, int, int]:
    """An AtlasTexture's `margin`, or all zeroes when it has none.

    The packer trims transparent edges off a sprite and records what it took in
    `margin`: the position is where the trimmed region sits inside the original
    image, the size is how much came off in total. Sprites are laid out by
    their *original* bounds, so a sprite pasted back without its margin sits in
    the wrong place — the card banner loses 23px off the top, which is enough
    to lift the whole ribbon clear of the title it is supposed to sit behind.
    """
    m = _RE_MARGIN.search(text)
    return tuple(int(float(g)) for g in m.groups()) if m else (0, 0, 0, 0)


def _restore_margin(piece, margin):
    """Put a cropped region back on a canvas the size the game expects."""
    mx, my, mw, mh = margin
    if not (mx or my or mw or mh):
        return piece
    from PIL import Image
    full = Image.new("RGBA", (piece.width + mw, piece.height + mh), (0, 0, 0, 0))
    full.paste(piece, (mx, my))
    return full


def _regions(f, index) -> dict[str, tuple[str, tuple[int, int, int, int], tuple]]:
    """{sprite path: (atlas res:// path, (x, y, w, h), margin)}."""
    out = {}
    for path in index:
        if not (path.startswith(SPRITE_PREFIX) and path.endswith(".tres")):
            continue
        text = _read(f, index, path).decode("utf-8", "replace")
        atlas, region = _RE_ATLAS.search(text), _RE_REGION.search(text)
        if atlas and region:
            slug = path[len(SPRITE_PREFIX):-len(".tres")]
            out[slug] = (atlas.group(1),
                         tuple(int(float(g)) for g in region.groups()),
                         _margin_of(text))
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


# A card whose art depends on a choice made during the run, so the archive has
# one sprite per face and none under the bare name. The catalogue has no run to
# read, so it shows the first face in the game's own choose(Attack|Skill|Power)
# order. Which face an actual copy wore is recoverable per run: the deck entry
# carries props.ints TinkerTimeType, 1/2/3 = Attack/Skill/Power.
_ART_VARIANTS = {"mad_science": "mad_science_attack"}


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
        for candidate in (key, key.replace(f"_{char}", ""),
                          _ART_VARIANTS.get(key, "")):
            if candidate in leaves:
                found[cid] = leaves[candidate]
                break
        else:
            missing.append(cid)
    return found, missing


def _filename(card_id: str) -> str:
    stem = card_id.split(".", 1)[1] if "." in card_id else card_id
    return re.sub(r"[^a-z0-9_]+", "_", stem.lower()) + ".png"


def _imported_textures(index) -> dict[str, str]:
    """{'foo.png': '.godot/imported/foo.png-<hash>.<variant>.ctex'}

    Godot writes one imported file per compression variant, and only the
    basename survives the import — the source folder is gone — so that is the
    only key available. A BC7 or BC3 variant wins over an uncompressed one
    because that is what the game actually ships for these.
    """
    out: dict[str, str] = {}
    for path in index:
        m = _RE_IMPORTED.match(path)
        if not m:
            continue
        key = f"{m.group(1)}.png"
        if key not in out or m.group(2) in (".bptc", ".s3tc"):
            out[key] = path
    return out


def _matmul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
                 for i in range(3))


def _invert3(m):
    det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
           - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
           + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    c = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            r = [row for k, row in enumerate(m) if k != i]
            minor = [[v for k, v in enumerate(row) if k != j] for row in r]
            cof = minor[0][0] * minor[1][1] - minor[0][1] * minor[1][0]
            c[j][i] = ((-1) ** (i + j)) * cof / det     # transposed => inverse
    return tuple(tuple(row) for row in c)


def _tint_matrix(h: float, s: float, v: float):
    """The whole of hsv.gdshader as one 3x3, since every step of it is linear.

    Hue rotates the I/Q plane by (1 - h) turns, saturation scales I and Q, and
    the value multiply scales all three. Folding them into a single matrix lets
    Pillow apply the tint in one pass with no numpy dependency, and — unlike
    CSS hue-rotate, which uses different luminance weights and the opposite
    rotation sense — reproduces the game's colours exactly.
    """
    theta = (1.0 - h) * 2.0 * math.pi
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    # Y unchanged; I' = I cos - Q sin; Q' = I sin + Q cos
    rotate = ((1.0, 0.0, 0.0), (0.0, cos_t, -sin_t), (0.0, sin_t, cos_t))
    scale = ((v, 0.0, 0.0), (0.0, s * v, 0.0), (0.0, 0.0, s * v))
    combined = _matmul(_invert3(_RGB_TO_YIQ),
                       _matmul(scale, _matmul(rotate, _RGB_TO_YIQ)))
    return combined


def _tint(img, h: float, s: float, v: float):
    """Apply a tint to an RGBA image, leaving alpha alone."""
    m = _tint_matrix(h, s, v)
    flat = (m[0][0], m[0][1], m[0][2], 0.0,
            m[1][0], m[1][1], m[1][2], 0.0,
            m[2][0], m[2][1], m[2][2], 0.0)
    alpha = img.getchannel("A")
    out = img.convert("RGB").convert("RGB", flat)
    out.putalpha(alpha)
    return out


def _region_of(f, index, tres_path: str):
    """(atlas res path, (x, y, w, h), margin) for one AtlasTexture resource."""
    if tres_path not in index:
        return None
    text = _read(f, index, tres_path).decode("utf-8", "replace")
    atlas = re.search(r'path="res://([^"]+\.png)"', text)
    region = _RE_REGION.search(text)
    if not (atlas and region):
        return None
    return (atlas.group(1),
            tuple(int(float(g)) for g in region.groups()),
            _margin_of(text))


def _extract_ui(f, index, ctex_for, out: Path, on_progress=None) -> int:
    """Write the frame furniture (frames, borders, banner, plaque, orbs)."""
    ui_out = out / UI_DIRNAME
    ui_out.mkdir(parents=True, exist_ok=True)

    wanted: dict[str, list[tuple[str, tuple, tuple]]] = {}
    for name, tres in UI_SPRITES.items():
        found = _region_of(f, index, tres)
        if not found:
            log.warning("UI sprite not in archive: %s", tres)
            continue
        atlas_png, rect, margin = found
        wanted.setdefault(atlas_png, []).append((name, rect, margin))

    written = 0
    for atlas_png, members in sorted(wanted.items()):
        key = Path(atlas_png).name
        if key not in ctex_for:
            log.warning("No imported texture for %s; skipping %d UI sprites",
                        atlas_png, len(members))
            continue
        if on_progress:
            on_progress(f"decoding {atlas_png} ({len(members)} UI sprites)")
        sheet = _decode_atlas(_read(f, index, ctex_for[key]))
        for name, (x, y, w, h), margin in members:
            piece = _restore_margin(sheet.crop((x, y, x + w, y + h)), margin)
            written += _write_sprite(piece, name, ui_out)
        sheet.close()

    for name, basename in sorted(UI_TEXTURES.items()):
        if basename not in ctex_for:
            log.warning("UI texture not in archive: %s", basename)
            continue
        if on_progress:
            on_progress(f"decoding {basename}")
        piece = _decode_atlas(_read(f, index, ctex_for[basename]))
        written += _write_sprite(piece, name, ui_out)
        piece.close()
    return written


def _write_sprite(piece, name: str, ui_out: Path) -> int:
    """Write a sprite and, where the game tints it, one file per tint.

    Frames take the character colour. Borders, banners and the type plaque all
    ship teal and take the rarity colour. The cost orbs ship in their final
    colour and are left alone.
    """
    piece.save(ui_out / f"{name}.png")
    tints = (FRAME_TINTS if name.startswith("frame_")
             else BANNER_TINTS if name.startswith(("portrait_", "banner", "plaque"))
             else None)
    if not tints:
        return 1
    for tint_name, (th, ts, tv) in tints.items():
        _tint(piece, th, ts, tv).save(ui_out / f"{name}__{tint_name}.png")
    return 1 + len(tints)


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

        ctex_for = _imported_textures(index)

        # Decode each atlas once, writing every card that lives on it before
        # moving on — three 16MB RGBA surfaces at once is not worth holding.
        by_atlas: dict[str, list[tuple[str, str]]] = {}
        for cid, slug in resolved.items():
            by_atlas.setdefault(regions[slug][0], []).append((cid, slug))

        written, skipped = 0, 0
        for atlas_png, members in sorted(by_atlas.items()):
            key = Path(atlas_png).name
            if key not in ctex_for:
                log.warning("No imported texture for %s; skipping %d cards",
                            atlas_png, len(members))
                skipped += len(members)
                continue
            if on_progress:
                on_progress(f"decoding {atlas_png} ({len(members)} cards)")
            sheet = _decode_atlas(_read(f, index, ctex_for[key]))
            for cid, slug in members:
                _, (x, y, w, h), margin = regions[slug]
                piece = _restore_margin(sheet.crop((x, y, x + w, y + h)), margin)
                piece.save(out / _filename(cid))
                written += 1
            sheet.close()

        ui_written = _extract_ui(f, index, ctex_for, out, on_progress)

    manifest = {
        "game_version": _game_version(game_dir),
        "generated": int(time.time()),
        "cards": {cid: _filename(cid) for cid in sorted(resolved)},
        "missing": sorted(missing),
        "ui": sorted([*UI_SPRITES, *UI_TEXTURES]),
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    return {
        "written": written,
        "skipped": skipped,
        "missing": len(missing),
        "total": len(resolved) + len(missing),
        "ui": ui_written,
        "game_version": manifest["game_version"],
        "out_dir": out,
        "seconds": round(time.time() - started, 1),
    }


# ── what the app reads ─────────────────────────────────────────────────────

# Status and Curse cards have no frame of their own in the archive; the game
# gives them the skill silhouette, so they follow it here.
_FRAME_BY_TYPE = {
    "attack": "frame_attack", "skill": "frame_skill", "power": "frame_power",
    "quest": "frame_quest", "status": "frame_skill", "curse": "frame_skill",
}
_BORDER_BY_TYPE = {
    "attack": "portrait_attack", "power": "portrait_power",
}
# The Ancient text panel ships in three heights, one per card type. Anything
# without its own (Status, Curse) follows the skill panel, as it does elsewhere.
_ANCIENT_TEXT_BG = {
    "attack": "ancient_text_bg_attack", "power": "ancient_text_bg_power",
}
_ENERGY_CHARACTERS = {
    "ironclad", "silent", "defect", "necrobinder", "regent", "colorless", "quest",
}

# The archive ships ONE frame sprite per shape, in red, and one banner in teal.
# Colour comes from a ShaderMaterial running shaders/hsv.gdshader — a YIQ hue
# rotation, then a saturation scale, then a multiply toward black. That is the
# same pipeline as CSS hue-rotate/saturate/brightness, so the CSS below
# reproduces it from the game's own material parameters rather than by eye.
#
# card.tscn settles which node takes which material, and it is not the obvious
# split: Frame takes the character colour, but PortraitBorder AND TitleBanner
# both take the *rarity* material. The type plaque is untinted.
#
# The one thing not read from the archive is which colour each character maps
# to — the game resolves that in compiled C#, so it is inferred here from the
# characters' established palette. Adjust if a card looks wrong.
_FRAME_COLOR_BY_CHARACTER = {
    "ironclad": "red", "silent": "green", "defect": "blue",
    "necrobinder": "pink", "regent": "orange", "curse": "curse",
    "quest": "quest",
}
_BANNER_BY_RARITY = {
    "common": "common", "uncommon": "uncommon", "rare": "rare",
    "ancient": "ancient", "curse": "curse", "event": "event",
    "quest": "quest", "status": "status", "starter": "common",
    "token": "common",
}


def _field(card, name: str) -> str:
    getter = getattr(card, "get", None)
    value = getter(name) if callable(getter) else getattr(card, name, "")
    return str(value or "")


def sprites_for(card) -> dict:
    """Which frame pieces a given card is built from.

    Ancient is a rarity rather than a type, and it does not merely recolour the
    normal card — it replaces the layout. The art bleeds to the card edge and
    the frame and portrait ring are gone entirely, so `ancient` is a branch the
    template takes rather than a different set of frame pieces. `frame` and
    `border` are still filled in, but nothing draws them for an Ancient card.
    """
    ctype = _field(card, "type").lower()
    rarity = _field(card, "rarity").lower()
    character = _field(card, "character").lower()
    ancient = rarity == "ancient"
    cost = _field(card, "cost")

    return {
        "ancient": ancient,
        "frame": _FRAME_BY_TYPE.get(ctype, "frame_skill"),
        "border": _BORDER_BY_TYPE.get(ctype, "portrait_skill"),
        "banner": "banner_ancient" if ancient else "banner",
        "text_bg": _ANCIENT_TEXT_BG.get(ctype, "ancient_text_bg_skill"),
        "energy": ("energy_" + character) if character in _ENERGY_CHARACTERS
                  else "energy_colorless",
        "frame_color": _FRAME_COLOR_BY_CHARACTER.get(character, "colorless"),
        "banner_color": _BANNER_BY_RARITY.get(rarity, "common"),
        "unplayable": cost.lower() == "unplayable",
        "cost": "" if cost.lower() == "unplayable" else cost,
        # Blank for everything that is not one of the 23 Regent cards charging
        # Stars, and the template draws the second orb only when it is filled.
        # No per-character variant to choose: the star orb is one sprite, since
        # only the Regent has the currency.
        "star_cost": _field(card, "star_cost"),
    }


# The game breaks a card's rules text at every sentence and picks its game
# terms out in gold. Its own strings carry both explicitly — MOLTEN_FIST reads
# "Deal {Damage:diff()} damage.\nDouble the enemy's [gold]Vulnerable[/gold]." —
# but localize.py strips the markup (TAG_RE) on the way to plain text, so both
# are reconstructed here: the break from the sentence end, the gold from the
# card's own `keywords`.
_RULES_BREAK = re.compile(r"(?<=\.)\s+|\n")
# Matching is case-sensitive on purpose. Game terms are capitalised in the
# text and the same word in lowercase is ordinary prose: "Whenever a card is
# Exhausted" is a keyword, "draw 1 card" is not. The optional tail lets an
# inflection match the stem it came from — Channeled, Wounds, Slimed, Souls.
_KEYWORD_TAIL = r"(?:s|es|d|ed|ing)?"


def _keywords(card) -> list[str]:
    getter = getattr(card, "get", None)
    value = getter("keywords") if callable(getter) else getattr(card, "keywords", None)
    return [str(k) for k in (value or []) if k]


def rules_lines(card) -> list[list[tuple[str, bool]]]:
    """Rules text as lines, each a list of (text, is_keyword) runs.

    Runs rather than HTML so escaping stays in the template.
    """
    text = _field(card, "description")
    words = sorted(set(_keywords(card)), key=len, reverse=True)
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(w) for w in words) + r")" + _KEYWORD_TAIL + r"\b"
    ) if words else None

    lines = []
    for sentence in _RULES_BREAK.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if pattern is None:
            lines.append([(sentence, False)])
            continue
        runs, pos = [], 0
        for m in pattern.finditer(sentence):
            if m.start() > pos:
                runs.append((sentence[pos:m.start()], False))
            runs.append((m.group(0), True))
            pos = m.end()
        if pos < len(sentence):
            runs.append((sentence[pos:], False))
        lines.append(runs)
    return lines


def text_length_class(description: str) -> str:
    """Bucket for the CSS that steps rules text down to fit the text box.

    Descriptions run to 122 characters at the longest, with a median of 49.
    """
    n = len(description or "")
    if n > 85:
        return "xlong"
    if n > 60:
        return "long"
    return ""

def load_manifest(out_dir=None) -> dict[str, str]:
    """{card id: filename} for art already on disk, or {} when none is."""
    path = Path(out_dir or art_dir()) / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cards = data.get("cards", {})
    return cards if isinstance(cards, dict) else {}
