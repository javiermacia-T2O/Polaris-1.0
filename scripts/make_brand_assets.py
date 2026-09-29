"""Generate Polaris brand assets from the source constellation artwork.

Inputs (workspace root):
    image_67112238-removebg-preview.png  -> transparent constellation (white)
    image_67112238.jpg                   -> same artwork on a dark background

Outputs:
    src-tauri/icons/icon.png             -> 512x512 app icon (navy + light blue)
    src-tauri/icons/icon.ico             -> multi-size Windows icon
    src/assets/polaris-logo.png          -> transparent light-blue logo for the UI
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "image_67112238-removebg-preview.png"
ICONS = ROOT / "src-tauri" / "icons"
ASSETS = ROOT / "src" / "assets"

NAVY = (16, 42, 76, 255)
LIGHT_BLUE = (90, 162, 224)
WHITE = (255, 255, 255)


def load_trimmed() -> Image.Image:
    """Return the constellation cropped to its visible bounds."""
    image = Image.open(SOURCE).convert("RGBA")
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    return image


def recolor(image: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """Replace every visible pixel with ``color`` keeping the alpha mask."""
    alpha = image.getchannel("A")
    solid = Image.new("RGBA", image.size, (*color, 255))
    solid.putalpha(alpha)
    return solid


def square(image: Image.Image, size: int, padding: float,
           background: tuple[int, int, int, int] | None) -> Image.Image:
    """Fit ``image`` inside a square canvas with proportional padding."""
    inner = int(size * (1 - 2 * padding))
    scale = min(inner / image.width, inner / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((size - resized.width) // 2,
                                     (size - resized.height) // 2))
    return canvas


def rounded_square(size: int, radius: int, color: tuple[int, int, int, int]) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    from PIL import ImageDraw
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1),
                                           radius=radius, fill=255)
    plate = Image.new("RGBA", (size, size), color)
    plate.putalpha(mask)
    return plate


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)

    trimmed = load_trimmed()

    # --- App icon: navy rounded plate + light-blue constellation -----------
    size = 512
    plate = rounded_square(size, radius=112, color=NAVY)
    mark = square(recolor(trimmed, LIGHT_BLUE), size, padding=0.24, background=None)
    icon = plate.copy()
    icon.alpha_composite(mark)
    icon.save(ICONS / "icon.png")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    icon.save(ICONS / "icon.ico", sizes=[(s, s) for s in sizes])

    # --- Sidebar logo: transparent, light blue ----------------------------
    logo = square(recolor(trimmed, LIGHT_BLUE), 256, padding=0.06, background=None)
    logo.save(ASSETS / "polaris-logo.png")

    # --- White variant for dark surfaces ----------------------------------
    white = square(recolor(trimmed, WHITE), 256, padding=0.06, background=None)
    white.save(ASSETS / "polaris-logo-white.png")

    print("icon.png", (ICONS / "icon.png").stat().st_size)
    print("icon.ico", (ICONS / "icon.ico").stat().st_size)
    print("polaris-logo.png", (ASSETS / "polaris-logo.png").stat().st_size)


if __name__ == "__main__":
    main()