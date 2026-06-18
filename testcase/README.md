# TTB Label Verifier - Test Data

## Overview
This folder contains test cases extracted from the official TTB malt beverage label examples PDF.

## Files
- `test_cases.csv` - Batch test data with image paths, expected fields, and ground truth
- `images/` - Label images (good & bad examples)
- `extracted_text.txt` - Raw text from PDF for reference

## Usage
1. Copy this folder to your project root
2. Load `test_cases.csv` in your app for batch processing
3. Images are referenced with relative paths (e.g. images/label_good_01.jpg)

## Test Scenarios Covered
- Contract Brewing (multiple scenarios)
- Growler / Crowler corrections
- Keg Collar issues
- Nutritional / Serving Facts
- Alcohol Facts
- Gluten statements
- Non-Alcoholic labels
- Government Warning variations

Copy images from the PDF screenshots as needed.
