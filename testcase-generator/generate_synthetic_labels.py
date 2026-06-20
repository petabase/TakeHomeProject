"""
Generate synthetic TTB label images + a matching metadata.csv for testing.

This produces PROGRAMMATIC test labels with known ground truth — useful for
an automated test suite, since we know exactly what should be extracted and
whether each case should PASS, FAIL, or NEEDS REVIEW.

For more visually realistic (but non-deterministic) test labels, see the
companion AI-generation guide at the bottom of this file's module docstring,
or scripts/generate_ai_labels_README.md.

Usage:
    python scripts/generate_test_labels.py --count 12 --out test_data/

Produces:
    test_data/
        images/
            label_001.png
            label_002.png
            ...
        metadata.csv
        ground_truth.csv   (includes expected_outcome column for test assertions)
"""

import argparse
import csv
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


GOVERNMENT_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, "
    "women should not drink alcoholic beverages during pregnancy "
    "because of the risk of birth defects. (2) Consumption of "
    "alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)

# A handful of plausible brand/class combinations to randomize from
BRANDS = [
    ("OLD TOM DISTILLERY", "Bourbon Whiskey", "45%", "750 mL"),
    ("STONE'S THROW", "Malt Beverage", "5.2%", "12 fl oz"),
    ("HARBOR LIGHT", "American Lager", "4.8%", "355 mL"),
    ("RED CEDAR FARMS", "Hard Cider", "6.5%", "500 mL"),
    ("BLUE RIDGE RESERVE", "Straight Rye Whiskey", "50%", "750 mL"),
    ("SILVER CREEK", "Vodka", "40%", "1 L"),
    ("COPPER KETTLE", "Single Malt Scotch Whisky", "43%", "750 mL"),
    ("NORTHWIND BREWING", "India Pale Ale", "6.8%", "473 mL"),
]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try common system font paths; fall back to PIL's bundled default."""
    candidates = [
        f"/usr/share/fonts/truetype/liberation/LiberationSans-{'Bold' if bold else 'Regular'}.ttf",
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        "/System/Library/Fonts/Helvetica.ttc",                 # macOS
        "/Library/Fonts/Arial.ttf",                              # macOS
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",  # Windows
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default(size=size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Simple greedy word-wrap for the Government Warning paragraph."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def generate_label(
    brand: str, class_type: str, abv: str, net_contents: str,
    defect: str | None = None
) -> tuple[Image.Image, dict]:
    """
    Draw one label image. `defect` controls deliberate non-compliance for
    negative test cases:
        None                  -> fully compliant label
        "lowercase_warning"   -> Government Warning in wrong case (should be LIKELY/REVIEW)
        "missing_warning"     -> no Government Warning at all (should FAIL)
        "wrong_abv"           -> ABV printed differs from application data (should FAIL)
        "blurry"              -> heavy blur applied (should be UNREADABLE on some fields)
    """
    W, H = 600, 800
    img = Image.new("RGB", (W, H), "#F5F0E6")
    draw = ImageDraw.Draw(img)

    title_font = _load_font(34, bold=True)
    sub_font = _load_font(20)
    body_font = _load_font(14)
    warning_font = _load_font(11)

    # Border
    draw.rectangle([20, 20, W - 20, H - 20], outline="#3a2a1a", width=4)

    # Brand name
    draw.text((W / 2, 110), brand, font=title_font, fill="#2a1a0a", anchor="mm")

    # Class/type
    draw.text((W / 2, 170), class_type, font=sub_font, fill="#3a2a1a", anchor="mm")

    # ABV — optionally wrong for the wrong_abv defect case
    printed_abv = "99%" if defect == "wrong_abv" else abv
    draw.text((W / 2, 230), f"ALCOHOL {printed_abv} BY VOLUME", font=body_font,
               fill="#3a2a1a", anchor="mm")

    # Net contents
    draw.text((W / 2, 260), net_contents, font=body_font, fill="#3a2a1a", anchor="mm")

    # Decorative divider
    draw.line([(60, 320), (W - 60, 320)], fill="#3a2a1a", width=2)

    # Government Warning block
    if defect != "missing_warning":
        warning_text = GOVERNMENT_WARNING
        if defect == "lowercase_warning":
            warning_text = warning_text[0].upper() + warning_text[1:].lower().replace(
                "government warning", "Government Warning", 1
            )
        lines = _wrap_text(draw, warning_text, warning_font, W - 120)
        y = 360
        for line in lines:
            draw.text((60, y), line, font=warning_font, fill="#2a1a0a")
            y += 18

    # Apply blur defect if requested
    if defect == "blurry":
        from PIL import ImageFilter
        img = img.filter(ImageFilter.GaussianBlur(radius=4))

    ground_truth = {
        "brand_name": brand,
        "class_type": class_type,
        "abv": abv,
        "net_contents": net_contents,
        "defect": defect or "none",
        "expected_outcome": {
            None: "PASS",
            "lowercase_warning": "NEEDS REVIEW",
            "missing_warning": "FAIL",
            "wrong_abv": "FAIL",
            "blurry": "NEEDS REVIEW",
        }[defect],
    }

    return img, ground_truth


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic TTB test labels.")
    parser.add_argument("--count", type=int, default=12,
                         help="Number of labels to generate (default 12)")
    parser.add_argument("--out", type=str, default="test_data",
                         help="Output directory (default test_data/)")
    parser.add_argument("--defect-rate", type=float, default=0.4,
                         help="Fraction of labels with a deliberate defect (default 0.4)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    defect_types = ["lowercase_warning", "missing_warning", "wrong_abv", "blurry"]

    metadata_rows = []
    ground_truth_rows = []

    for i in range(1, args.count + 1):
        brand, class_type, abv, net_contents = random.choice(BRANDS)
        defect = random.choice(defect_types) if random.random() < args.defect_rate else None

        filename = f"label_{i:03d}.png"
        img, gt = generate_label(brand, class_type, abv, net_contents, defect)
        img.save(images_dir / filename)

        metadata_rows.append({
            "filename": filename,
            "brand_name": brand,
            "class_type": class_type,
            "abv": abv,
            "net_contents": net_contents,
            "government_warning": "true",
        })
        ground_truth_rows.append({"filename": filename, **gt})

    with open(out_dir / "metadata.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(metadata_rows[0].keys()))
        writer.writeheader()
        writer.writerows(metadata_rows)

    with open(out_dir / "ground_truth.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ground_truth_rows[0].keys()))
        writer.writeheader()
        writer.writerows(ground_truth_rows)

    defect_count = sum(1 for r in ground_truth_rows if r["defect"] != "none")
    print(f"Generated {args.count} labels in {images_dir}/")
    print(f"  {args.count - defect_count} compliant, {defect_count} with deliberate defects")
    print(f"Metadata CSV (upload this with the images): {out_dir / 'metadata.csv'}")
    print(f"Ground truth CSV (for verifying test results): {out_dir / 'ground_truth.csv'}")


if __name__ == "__main__":
    main()
