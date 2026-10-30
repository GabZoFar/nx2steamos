# Steam library artwork for a ported game, made from its own icon (nx_extract saves the control data's icon as icon.jpg, 256x256): Steam's grid images for a shortcut are
# a portrait capsule (600x900), a wide capsule (920x430), a hero banner (1920x620) and the shortcut's icon. Each is the icon over a blurred, darkened fill of itself.
# No logo: without one Steam writes the game's name over the hero. Nothing is fetched: the player's own game files are the only source.
from pathlib import Path

SIZES = {'portrait': (600, 900), 'wide': (920, 430), 'hero': (1920, 620)}


def _fill(img, size):   # the icon scaled to cover size, blurred and darkened: a background in the game's own colours
    from PIL import Image, ImageEnhance, ImageFilter
    w, h = size; s = max(w / img.width, h / img.height) * 1.15
    big = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
    x, y = (big.width - w) // 2, (big.height - h) // 2
    bg = big.crop((x, y, x + w, y + h)).filter(ImageFilter.GaussianBlur(radius=max(w, h) / 30))
    return ImageEnhance.Brightness(bg).enhance(0.55)


def _rounded(img, radius):
    from PIL import Image, ImageDraw
    mask = Image.new('L', img.size, 0); ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    out = img.convert('RGBA'); out.putalpha(mask)
    return out


def _paste_icon(bg, icon, side, center):
    from PIL import Image, ImageFilter
    ic = _rounded(icon.resize((side, side), Image.LANCZOS), side // 10)
    shadow = Image.new('RGBA', (side + 60, side + 60), (0, 0, 0, 0)); shadow.paste((0, 0, 0, 150), (30, 36), ic)
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    cx, cy = center
    bg.paste(shadow, (cx - side // 2 - 30, cy - side // 2 - 30), shadow)
    bg.paste(ic, (cx - side // 2, cy - side // 2), ic)


def make(icon_jpg, out_dir):
    """The artwork PNGs in out_dir from the game's icon: {'portrait', 'wide', 'hero', 'icon'} → path. {} without Pillow or an icon"""
    try: from PIL import Image
    except ImportError: return {}
    icon_jpg = Path(icon_jpg)
    if not icon_jpg.is_file(): return {}
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    icon = Image.open(icon_jpg).convert('RGB')
    out = {}
    for kind, (w, h) in SIZES.items():
        bg = _fill(icon, (w, h)).convert('RGBA')
        if kind == 'portrait': _paste_icon(bg, icon, int(w * 0.72), (w // 2, int(h * 0.42)))
        elif kind == 'wide': _paste_icon(bg, icon, int(h * 0.74), (w // 2, h // 2))
        else: _paste_icon(bg, icon, int(h * 0.6), (int(w * 0.18), h // 2))   # the hero's middle is where Steam puts the name
        p = out_dir / f'{kind}.png'; bg.convert('RGB').save(p, optimize=True); out[kind] = p
    p = out_dir / 'icon.png'; icon.resize((256, 256), Image.LANCZOS).save(p); out['icon'] = p
    return out
