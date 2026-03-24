import json
from utils import ingredient_parser
from utils import nutrition
from utils import category_mapper

def clean_string(val) -> str:
    """Ensures a value is a string and strips whitespace."""
    return str(val).strip() if val else ""

def extract_hsr(attrs: dict) -> float:
    """Safely extracts the Health Star Rating as a float."""
    hsr_str = attrs.get('healthstarrating')
    if hsr_str:
        try:
            return float(hsr_str)
        except ValueError:
            pass
    return None

def normalize(data: dict):
    """Maps the raw Woolworths payload directly into the strict iOS ProductJSON schema."""
    for bundle in (data.get("Products") or []):
        for p in bundle.get("Products", []):
            attrs = p.get("AdditionalAttributes", {}) or {}
            
            # Skip non-food marketplace items
            if p.get("IsMarketProduct", False):
                continue

            gtin = clean_string(p.get("Barcode"))
            raw_ingredients = clean_string(attrs.get('ingredients'))
            
            # Use the V2 NLP Parser (returns a flat list of strings natively)
            flat_ingredients = ingredient_parser.parse_ingredients(raw_ingredients) if raw_ingredients else []
            
            macros_100g, _, serving_size, servings_per_pack = nutrition.parse_woolworths(attrs.get('nutritionalinformation'))
            
            cat_name = clean_string(attrs.get('sapdepartmentname'))
            sub_name = clean_string(attrs.get('sapcategoryname'))
            canonical_cat, canonical_sub = category_mapper.map_woolworths(cat_name, sub_name)

            return {
                "gtin": gtin,
                "source": "woolworths_api",
                "brand": clean_string(p.get("Brand")),
                "name": clean_string(p.get("DisplayName") or p.get("Name")),
                "image_url": clean_string(p.get("MediumImageFile")),
                "package_size": clean_string(p.get("PackageSize")),
                "category": canonical_cat,
                "subcategory": canonical_sub,
                "health_star_rating": extract_hsr(attrs),
                "serving_size_g": serving_size,
                "servings_per_pack": servings_per_pack,
                
                "nutrition_100g": macros_100g if macros_100g else None,
                "raw_ingredients": raw_ingredients,
                "parsed_ingredients": flat_ingredients,
                
                # Baseline Clinical Profile for un-enriched live scans
                "clinical_profile": None,
                
                # Baseline Metadata
                "_source_id": gtin,
                "_source_name": "woolworths_api",
                "_enrichment_llm": "pending"
            }
            
    return None
