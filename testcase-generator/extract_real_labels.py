"""
Extract real label test images from TTB's own official malt beverage
labeling guide PDF, and build a matching metadata CSV + ground truth CSV
for batch testing.

Source document: TTB G 2023-9, "Malt Beverage Label Examples"
https://www.ttb.gov/system/files/images/beer/labeling/malt-beverage-example-labels.pdf
(also archived at docs/malt-beverage-example-labels.pdf in this repo)

Why use this PDF specifically: it contains TTB's own published
compliant/non-compliant label pairs, each with the agency's own stated
reasoning for rejection (citing specific CFR sections). This is stronger
ground truth than anything we could synthesize — these are the actual
defects TTB investigators are trained to catch, in TTB's own words.

What this script does:
    1. Extracts the embedded label artwork images directly from the PDF
       (not a screenshot/rasterization — pulls the actual embedded image
       objects, so crops are full resolution with no surrounding page text)
    2. Selects only the scenarios relevant to the fields this app checks
       (brand name, class/type, ABV, net contents, Government Warning) —
       skips the nutritional/gluten/serving-facts scenarios, which test
       fields outside this app's current scope
    3. Applies deliberate angle/blur/glare distortion to roughly half the
       images, so the resulting set tests both field-matching correctness
       AND image-quality tolerance
    4. Writes batch-metadata.csv (the application data each label SHOULD match)
       and batch-results.csv (expected PASS/FAIL outcome + TTB's own stated
       reasoning, for verifying the app's output against)
    5. Zips the image folder for testing the app's batch .zip upload path

Usage:
    python testcase-generator/extract_real_labels.py \\
        --pdf docs/malt-beverage-example-labels.pdf \\
        --out testcase/batchLabels

Requires: pymupdf, Pillow (not in the main app's requirements.txt —
install separately: pip install pymupdf pillow --break-system-packages)
"""

import argparse
import csv
import os
import random
import shutil
import zipfile
from pathlib import Path

import fitz  # pymupdf
from PIL import Image, ImageDraw, ImageFilter


# Page -> (scenario_slug, compliant_image_filename, noncompliant_image_filename_or_None)
# Determined by inspecting embedded image objects per page in the source PDF.
# Pages 3-7 show only a single (compliant) label each. Pages 8+ show paired
# non-compliant (left, red X) / compliant (right, green check) labels.
#
# Only scenarios relevant to this app's checked fields are included here.
# The source PDF also covers nutritional claims, serving facts, alcohol
# facts, and gluten statements (pages 11-15) — those test fields this app
# does not currently extract/compare, so they're intentionally excluded.
SCENARIO_MAP = {
    3: ("contract_brewing_scenario1_dba", "page03_img1", None),
    4: ("contract_brewing_scenario2_multiloc", "page04_img1", None),
    5: ("contract_brewing_scenario3_two_locations", "page05_img1", None),
    8: ("growler", "page08_img2", "page08_img1"),
    9: ("crowler", "page09_img2", "page09_img1"),
    10: ("keg_collar", "page10_img2", "page10_img1"),
}

# What the application data SHOULD say for each scenario, per the source
# document's own description of each compliant label.
APPLICATION_DATA = {
    "contract_brewing_scenario1_dba": {
        "brand_name": "Example Brewing Co.", "class_type": "Wheat Beer",
        "abv": "4% ALC./VOL.", "net_contents": "1 PINT",
    },
    "contract_brewing_scenario2_multiloc": {
        "brand_name": "Example Brewing Co.", "class_type": "Wheat Beer",
        "abv": "4% ALC./VOL.", "net_contents": "1 PINT",
    },
    "contract_brewing_scenario3_two_locations": {
        "brand_name": "Example Brewing Co.", "class_type": "Wheat Beer",
        "abv": "4% ALC./VOL.", "net_contents": "1 PINT",
    },
    "growler": {
        "brand_name": "Malt & Hop Brewery", "class_type": "Beer",
        "abv": "4% ALC./VOL.", "net_contents": "1 QT.",
    },
    "crowler": {
        "brand_name": "Example Brewing Company", "class_type": "Beer",
        "abv": "5% ALC/VOL", "net_contents": "1 QT.",
    },
    "keg_collar": {
        "brand_name": "Example Brewing Co.", "class_type": "Axel Cream Ale",
        "abv": "5%", "net_contents": "15.5 GAL",
    },
}

# TTB's own stated reasoning for each non-compliant label (paraphrased from
# the source document's "Corrections" sections under each scenario).
NONCOMPLIANT_REASONS = {
    "growler": (
        "Net contents incorrectly shown as 32 OZ instead of the required "
        "1 QT (27 CFR 7.70); class/type designation missing (Name/Style "
        "field left blank)."
    ),
    "crowler": (
        "Net contents incorrectly shown as 32 OZ instead of 1 QT; ABV "
        "abbreviated as non-compliant \"ABV\" instead of ALC/VOL; "
        "class/type designation missing."
    ),
    "keg_collar": (
        "Government Warning has lowercase \"surgeon general\" instead of "
        "\"Surgeon General\" and is missing required commas after "
        "\"General\" and \"machinery\"; ABV left blank; class/type not "
        "selected from the available checkboxes."
    ),
}

# Deliberate distortion plan: ensures both compliant and non-compliant
# labels get distorted examples, and all three distortion types appear at
# least once across the set.
DISTORTION_PLAN = {
    "contract_brewing_scenario1_dba__compliant": "angle",
    "contract_brewing_scenario3_two_locations__compliant": "glare",
    "growler__noncompliant": "blur",
    "crowler__compliant": "angle",
    "keg_collar__noncompliant": "glare",
}


def extract_embedded_images(pdf_path: Path, work_dir: Path) -> Path:
    """Pull every embedded image object out of the PDF, named by page/index."""
    extracted_dir = work_dir / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    for page_idx, page in enumerate(doc, start=1):
        for img_idx, img in enumerate(page.get_images(full=True), start=1):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            if pix.n - pix.alpha >= 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            out_path = extracted_dir / f"page{page_idx:02d}_img{img_idx}.png"
            pix.save(str(out_path))
    return extracted_dir


def select_relevant_labels(extracted_dir: Path, out_images_dir: Path) -> None:
    """Copy + rename only the scenario images relevant to this app's checked fields."""
    out_images_dir.mkdir(parents=True, exist_ok=True)

    for page, (slug, compliant_prefix, noncompliant_prefix) in SCENARIO_MAP.items():
        matches = list(extracted_dir.glob(f"{compliant_prefix}.png"))
        if not matches:
            print(f"⚠️  Warning: no image found for {compliant_prefix} (page {page})")
            continue
        shutil.copy(matches[0], out_images_dir / f"{slug}__compliant.png")

        if noncompliant_prefix:
            matches = list(extracted_dir.glob(f"{noncompliant_prefix}.png"))
            if matches:
                shutil.copy(matches[0], out_images_dir / f"{slug}__noncompliant.png")


def apply_angle(img: Image.Image, max_deg: float = 12) -> Image.Image:
    """Simulate a label photographed at a slight angle on a table."""
    angle = random.uniform(-max_deg, max_deg)
    rotated = img.convert("RGBA").rotate(angle, expand=True, resample=Image.BICUBIC)
    bg = Image.new("RGBA", rotated.size, (235, 230, 220, 255))
    bg.paste(rotated, (0, 0), rotated)
    return bg.convert("RGB")


def apply_blur(img: Image.Image, radius: float = 3.5) -> Image.Image:
    """Simulate an out-of-focus phone photo."""
    return img.filter(ImageFilter.GaussianBlur(radius=radius))


def apply_glare(img: Image.Image) -> Image.Image:
    """Simulate glare off a glass bottle under indoor lighting."""
    img = img.convert("RGBA")
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cx, cy = int(w * random.uniform(0.3, 0.6)), int(h * random.uniform(0.15, 0.35))
    rx, ry = int(w * 0.28), int(h * 0.18)
    for i in range(8, 0, -1):
        alpha = int(18 * i)
        draw.ellipse(
            [cx - rx * i / 8, cy - ry * i / 8, cx + rx * i / 8, cy + ry * i / 8],
            fill=(255, 255, 255, alpha)
        )
    return Image.alpha_composite(img, overlay).convert("RGB")


DISTORTION_FUNCS = {"angle": apply_angle, "blur": apply_blur, "glare": apply_glare}


def apply_distortions(images_dir: Path) -> None:
    """Create distorted variants per DISTORTION_PLAN, alongside the originals."""
    for base_name, distortion in DISTORTION_PLAN.items():
        src = images_dir / f"{base_name}.png"
        if not src.exists():
            print(f"⚠️  Warning: {src} not found, skipping distortion")
            continue
        img = Image.open(src).convert("RGB")
        result = DISTORTION_FUNCS[distortion](img)
        out_path = images_dir / f"{base_name}__{distortion}.png"
        result.save(out_path)
        print(f"  Created: {out_path.name}")


def write_metadata_csv(images_dir: Path, out_path: Path) -> None:
    """One row per image, with the application data it should match."""
    rows = []
    for img_path in sorted(images_dir.glob("*.png")):
        # filename pattern: {scenario}__{compliant|noncompliant}[__{distortion}].png
        scenario = img_path.stem.split("__")[0]
        if scenario not in APPLICATION_DATA:
            continue
        data = APPLICATION_DATA[scenario]
        rows.append({
            "filename": img_path.name,
            "brand_name": data["brand_name"],
            "class_type": data["class_type"],
            "abv": data["abv"],
            "net_contents": data["net_contents"],
            "government_warning": "true",
        })

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_ground_truth_csv(images_dir: Path, out_path: Path) -> None:
    """One row per image, with expected PASS/FAIL and TTB's own stated reasoning."""
    rows = []
    for img_path in sorted(images_dir.glob("*.png")):
        stem_parts = img_path.stem.split("__")
        scenario = stem_parts[0]
        compliance = stem_parts[1] if len(stem_parts) > 1 else "unknown"
        distortion = stem_parts[2] if len(stem_parts) > 2 else "none"

        if scenario not in APPLICATION_DATA:
            continue

        expected = "PASS" if compliance == "compliant" else "FAIL"
        reason = (
            "Compliant example as published by TTB — all fields match."
            if expected == "PASS"
            else NONCOMPLIANT_REASONS.get(scenario, "See source PDF for stated defects.")
        )

        rows.append({
            "filename": img_path.name,
            "expected_outcome": expected,
            "distortion": distortion,
            "source": "TTB malt beverage label guide (TTB G 2023-9)",
            "reason": reason,
        })

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def zip_images(images_dir: Path, out_zip_path: Path) -> None:
    """Zip the images folder for testing the app's batch .zip upload path."""
    with zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for img_path in sorted(images_dir.glob("*.png")):
            zf.write(img_path, arcname=f"images/{img_path.name}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract real TTB label test data from the official malt beverage labeling guide PDF."
    )
    parser.add_argument("--pdf", type=str, required=True,
                         help="Path to the source PDF (e.g. docs/malt-beverage-example-labels.pdf)")
    parser.add_argument("--out", type=str, default="testcase/batchLabels",
                         help="Output directory (default: testcase/batchLabels)")
    parser.add_argument("--seed", type=int, default=42,
                         help="Random seed for distortion reproducibility (default: 42)")
    args = parser.parse_args()

    random.seed(args.seed)

    pdf_path = Path(args.pdf)
    out_dir = Path(args.out)
    work_dir = out_dir / "_work"
    images_dir = out_dir / "images"

    print(f"Extracting embedded images from {pdf_path} ...")
    extracted_dir = extract_embedded_images(pdf_path, work_dir)

    print("Selecting scenarios relevant to checked fields (brand, class/type, ABV, net contents, warning) ...")
    select_relevant_labels(extracted_dir, images_dir)

    print("Applying deliberate angle/blur/glare distortions to a subset ...")
    apply_distortions(images_dir)

    print("Writing batch-metadata.csv ...")
    write_metadata_csv(images_dir, out_dir / "batch-metadata.csv")

    print("Writing batch-results.csv (ground truth) ...")
    write_ground_truth_csv(images_dir, out_dir / "batch-results.csv")

    zip_path = out_dir / "ttb_real_labels_images.zip"
    print(f"Zipping images to {zip_path} ...")
    zip_images(images_dir, zip_path)

    shutil.rmtree(work_dir, ignore_errors=True)

    image_count = len(list(images_dir.glob("*.png")))
    print(f"\n✅ Done. {image_count} images in {images_dir}/")
    print(f"   Upload batch-metadata.csv + {zip_path.name} together to test the batch flow.")
    print(f"   Compare app output against batch-results.csv to verify correctness.")


if __name__ == "__main__":
    main()
