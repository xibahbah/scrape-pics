"""Build the transparent Jade app icon from the supplied artwork."""

from pathlib import Path
import sys

from PIL import Image, ImageDraw


SOURCE = Path(sys.argv[1])
ROOT = Path(__file__).resolve().parents[1]
SIZE = 1024


def main() -> None:
    artwork = Image.open(SOURCE).convert("RGB")
    # The supplied render includes a black presentation margin. Crop to the
    # actual rounded Jade tile before masking its outside corners transparent.
    tile = artwork.crop((90, 78, 1165, 1153)).resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=184, fill=255)

    icon = tile.convert("RGBA")
    icon.putalpha(mask)
    icon.save(ROOT / "web" / "jade-brand.png")

    # The alternate studio lockup is intentionally wide. Lift only the dark
    # letterforms from its flat background so it rests cleanly in the sidebar.
    wordmark_source = Image.open(ROOT / "web" / "jade-wordmark-source.png").convert("RGB")
    wordmark = wordmark_source.crop((390, 300, 1145, 610))
    wordmark.save(ROOT / "web" / "jade-wordmark.png")

    iconset = ROOT / ".build" / "Jade-v3.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for filename, size in sizes.items():
        icon.resize((size, size), Image.Resampling.LANCZOS).save(iconset / filename)


if __name__ == "__main__":
    main()
