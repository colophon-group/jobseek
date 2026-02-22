import argparse
from pathlib import Path
from PIL import Image

THRESHOLD = 150

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOMAIN_DIR = ROOT / "public" / "publicdomain"
MASTER_DIR = PUBLIC_DOMAIN_DIR / "master"


def boost_alpha(a: int, factor: float = 1.2) -> int:
    """
    Increase separation: alphas above mid get higher,
    alphas below mid get lower.
    factor > 1 -> more contrast.
    """
    x = a / 255.0
    x = (x - 0.5) * factor + 0.5
    if x < 0:
        x = 0
    if x > 1:
        x = 1
    return int(x * 255)


def make_line_version(src_path: Path, out_path: Path, threshold=150, mode="dark"):
    im = Image.open(src_path).convert("L")
    w, h = im.size
    out = Image.new("RGBA", (w, h))

    src_px = im.load()
    out_px = out.load()

    for y in range(h):
        for x in range(w):
            v = src_px[x, y]  # 0=black, 255=white

            if v >= threshold:
                out_px[x, y] = (0, 0, 0, 0)
            else:
                # base alpha from darkness
                alpha = 255 - v
                # boost contrast on alpha
                alpha = boost_alpha(alpha)

                if mode == "dark":
                    out_px[x, y] = (0, 0, 0, alpha)
                else:
                    out_px[x, y] = (255, 255, 255, alpha)

    out.save(out_path, "PNG")


def needs_processing(src: Path, dark_out: Path, light_out: Path) -> bool:
    if not dark_out.exists() or not light_out.exists():
        return True
    src_mtime = src.stat().st_mtime
    return dark_out.stat().st_mtime < src_mtime or light_out.stat().st_mtime < src_mtime


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate dark/light PNG variants from master JPEGs.")
    parser.add_argument("--all", action="store_true", help="Re-process all images, not just new/modified ones.")
    args = parser.parse_args()

    paths = sorted(MASTER_DIR.glob("*.jpg"))

    for p in paths:
        src = Path(p)
        stem = src.stem

        dark_out = PUBLIC_DOMAIN_DIR / f"{stem}_dark.png"
        light_out = PUBLIC_DOMAIN_DIR / f"{stem}_light.png"

        if not args.all and not needs_processing(src, dark_out, light_out):
            print(f"  skip {stem}")
            continue

        print(f"  process {stem}")
        make_line_version(src, dark_out, threshold=THRESHOLD, mode="dark")
        make_line_version(src, light_out, threshold=THRESHOLD, mode="light")
