"""Icônes des applications Android (téléphone et TV) et de l'application macOS, faites à partir du logo
(static/icons/icon-512.png, logo sur fond transparent). À relancer si le logo change :

    python3 android/make_icons.py

Android 8+ : icône adaptative (fond uni + logo à part) : le lanceur la découpe à sa forme (rond, carré arrondi…)
sans l'entourer de blanc. Le logo tient dans la zone toujours visible (66 dp sur 108). Une version d'une seule
couleur sert aux icônes à thème d'Android 13.
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO = os.path.join(ROOT, "static", "icons", "icon-512.png")
BG = (11, 11, 14, 255)  # fond de Noxa
DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


def logo():
    im = Image.open(LOGO).convert("RGBA")
    return im.crop(im.split()[3].getbbox())


def fit(im, box):
    """Le logo réduit pour tenir dans un carré de `box` pixels (proportions gardées)."""
    s = box / max(im.size)
    return im.resize((max(1, round(im.size[0] * s)), max(1, round(im.size[1] * s))), Image.LANCZOS)


def centered(size, im):
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.alpha_composite(im, ((size - im.size[0]) // 2, (size - im.size[1]) // 2))
    return out


def android(module):
    res = os.path.join(ROOT, "android", module, "src", "main", "res")
    src = logo()
    for d, k in DENSITIES.items():
        folder = os.path.join(res, f"mipmap-{d}")
        os.makedirs(folder, exist_ok=True)
        size = round(108 * k)
        fg = centered(size, fit(src, round(size * 0.5)))  # 54 dp sur 108 : marge dans la zone visible de 66 dp
        fg.save(os.path.join(folder, "ic_launcher_foreground.png"), optimize=True)
        mono = Image.new("RGBA", fg.size, (255, 255, 255, 0))
        mono.putalpha(fg.split()[3].point(lambda a: 255 if a > 90 else 0))
        mono.save(os.path.join(folder, "ic_launcher_monochrome.png"), optimize=True)
        # icône d'avant Android 8 (et de secours) : carré plein, sans transparence autour
        legacy = Image.new("RGBA", (round(48 * k),) * 2, BG)
        lg = fit(src, round(48 * k * 0.66))
        legacy.alpha_composite(lg, ((legacy.size[0] - lg.size[0]) // 2, (legacy.size[1] - lg.size[1]) // 2))
        legacy.save(os.path.join(folder, "ic_launcher.png"), optimize=True)
    any_dpi = os.path.join(res, "mipmap-anydpi-v26")
    os.makedirs(any_dpi, exist_ok=True)
    with open(os.path.join(any_dpi, "ic_launcher.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n'
                '<!-- icône adaptative : fond uni + logo (android/make_icons.py) -->\n'
                '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
                '    <background android:drawable="@color/ic_launcher_background" />\n'
                '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
                '    <monochrome android:drawable="@mipmap/ic_launcher_monochrome" />\n'
                '</adaptive-icon>\n')
    values = os.path.join(res, "values")
    os.makedirs(values, exist_ok=True)
    with open(os.path.join(values, "ic_launcher_background.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                '    <color name="ic_launcher_background">#0B0B0E</color>\n</resources>\n')


def macos():
    """macOS : carré arrondi de la taille des icônes d'Apple (824 px dans 1024), le logo au centre."""
    size, square = 1024, 824
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (square, square), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, square - 1, square - 1), radius=185, fill=255)
    tile = Image.new("RGBA", (square, square), BG)
    tile.putalpha(mask)
    out.alpha_composite(tile, ((size - square) // 2, (size - square) // 2))
    lg = fit(logo(), round(square * 0.62))
    out.alpha_composite(lg, ((size - lg.size[0]) // 2, (size - lg.size[1]) // 2))
    folder = os.path.join(ROOT, "apps", "desktop", "assets")
    os.makedirs(folder, exist_ok=True)
    out.save(os.path.join(folder, "icon-mac.png"), optimize=True)


if __name__ == "__main__":
    android("mobile")
    android("app")
    macos()
    print("icônes écrites")
