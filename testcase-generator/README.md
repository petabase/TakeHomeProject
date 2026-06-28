# Test Case Generator

Two complementary ways to generate test data for the TTB Label Verifier, producing CSV + image batches ready to upload through the app's normal `/verify` flow.

## 1. Real TTB label examples — `extract_real_labels.py`

Pulls label images directly out of TTB's own official guide, **[Malt Beverage Label Examples (TTB G 2023-9)](https://www.ttb.gov/system/files/images/beer/labeling/malt-beverage-example-labels.pdf)** — archived at [`docs/malt-beverage-example-labels.pdf`](../docs/malt-beverage-example-labels.pdf) in this repo.

This is the strongest ground truth available: these are TTB's own published compliant/non-compliant label pairs, with the agency's own stated reasoning for each rejection (citing specific CFR sections). Only the scenarios relevant to this app's checked fields are included — contract brewing (brand/class-type), growlers and crowlers (net contents, class/type), and keg collars (Government Warning exact-match, ABV).

```bash
pip install pymupdf pillow --break-system-packages
python testcase-generator/extract_real_labels.py \
    --pdf docs/malt-beverage-example-labels.pdf \
    --out testcase/batchLabels
```

Produces:
```
testcase/batchLabels/
├── images/                      # 14 PNGs: 9 clean crops + 5 distorted variants
├── batch-metadata.csv           # application data each label should match
├── batch-results.csv            # expected PASS/FAIL + TTB's own stated reasoning (ground truth)
└── ttb_real_labels_images.zip   # the images/ folder, pre-zipped for batch upload
```

**Why some images are distorted:** roughly a third of the set has deliberate angle rotation, gaussian blur, or a simulated glare patch applied — testing image-quality tolerance, not just field-matching correctness, since Jenny's interview notes specifically called out imperfectly-photographed labels as a real-world condition. The distortion plan deliberately covers both compliant and non-compliant labels, so a distorted defect still needs to be caught, and a distorted compliant label shouldn't be wrongly flagged.

**To run this test case and verify the app's output**, see [`testcase/README.md`](../testcase/README.md) — it walks through uploading `batch-metadata.csv` + `ttb_real_labels_images.zip` and comparing the exported results against `batch-results.csv`.

## 2. Synthetic labels with known ground truth — `generate_synthetic_labels.py`

Programmatically draws label images with Pillow, where every field value is set by the script itself — useful for generating a larger or more varied test set on demand, or for isolating a single defect type (e.g. only Government Warning case errors) without depending on what TTB happened to publish examples of.

```bash
python testcase-generator/generate_synthetic_labels.py --count 12 --out testcase/synthetic
```

See the script's own docstring for the full defect-type list and usage options.

## When to use which

| | Real labels | Synthetic labels |
|---|---|---|
| Ground truth source | TTB's own published guidance | This script's own field values |
| Visual realism | High — actual label artwork | Lower — simple programmatic layout |
| Defect variety | Fixed to what TTB's guide covers | Fully controllable, easy to extend |
| Best for | Validating against real regulatory judgment | Stress-testing specific matching rules at volume |
