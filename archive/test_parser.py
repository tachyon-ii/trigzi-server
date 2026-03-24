#!/usr/bin/env python3

import json
from utils.ingredient_parser import parse_ingredient_tree

def run_tests():
    # 1..n test strings
    test_strings = [
        # Test 1: Deep nesting and percentages
        "Whole Grain Oats, Fruit 10% (Berries Cranberries 2% (Cranberries, Sugar, Sunflower Oil), Goji Berries, Blueberries 1% (Blueberries, Sugar, Sunflower Oil), Currants, Coconut), Nuts 9% (Almonds, Pecans), Golden Syrup, Seeds 8% (Sunflower, Sesame, Pepitas), Sunflower Oil, Cinnamon, Vitamin (Vitamin E).",
        
        # Test 2: OCR errors and open-ended colons
        "Sugar, Flavourings: Liquorice Extract, Menthol, Eu,calyptus Oil, Capsicum Tincture, Thickeners: Dextr,in, Gum Tragacanth"
    ]

    print("=== Running Ingredient Parser Tests ===\n")

    for i, test_str in enumerate(test_strings, 1):
        print(f"--- Test String {i} ---")
        print(f"RAW INPUT:\n{test_str}\n")
        
        try:
            # Run the parser
            parsed_tree = parse_ingredient_tree(test_str)
            
            # Pretty print the resulting dictionary
            print("PARSED OUTPUT:")
            print(json.dumps(parsed_tree, indent=2, ensure_ascii=False))
            
        except Exception as e:
            print(f"ERROR parsing string: {e}")
            
        print("\n" + "="*60 + "\n")

if __name__ == "__main__":
    run_tests()
