#!/usr/bin/env python3
"""
=============================================================================
Module:        Category Mapper
Location:      utils/category_mapper.py
Description:   Maps source-specific category strings (Woolworths SAP,
               Coles native) to a canonical category/subcategory taxonomy.

Architecture Note:
Canonical categories are defined by Coles category/subcategory pairs —
Coles is the reference taxonomy and its values pass through unchanged.
Woolworths uses SAP department/category/segment, mapped here via two
lookup tables (_WW_MAP for exact matches, _WW_DEPT_FALLBACK for
department-only fallbacks). New supermarket providers add their own
mapper function alongside; the canonical (cat, sub) tuple is the
contract every provider returns.

Usage:
    from utils.category_mapper import map_woolworths, map_coles

    cat, sub = map_woolworths(sap_department, sap_category, sap_segment)
    cat, sub = map_coles(coles_category, coles_subcategory)
=============================================================================
"""

from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Woolworths SAP → Canonical (category, subcategory)
# Key: (SapDepartmentName.upper(), SapCategoryName.upper())
# Value: (canonical_category, canonical_subcategory)
#
# Where subcategory is unknown/unmappable, use "" (empty string matches
# the Coles convention for uncategorised subcategories within a category).
# ---------------------------------------------------------------------------

_WW_MAP: Dict[Tuple[str, str], Tuple[str, str]] = {

    # GROCERIES
    ("GROCERIES", "BISCUITS"):                      ("Chips, Chocolates & Snacks", "Crackers & Crispbreads"),
    ("GROCERIES", "BREAKFAST CEREALS"):             ("Pantry", "Breakfast Cereal"),
    ("GROCERIES", "CANNED FISH"):                   ("Pantry", "Fish & Seafood"),
    ("GROCERIES", "CANNED FRUIT"):                  ("Pantry", "Canned Fruit"),
    ("GROCERIES", "CANNED MEAT"):                   ("Pantry", "Canned Meat"),
    ("GROCERIES", "CANNED VEGETABLES"):             ("Pantry", "Canned Vegetables"),
    ("GROCERIES", "COFFEE"):                        ("Pantry", "Instant Coffee"),
    ("GROCERIES", "CONFECTIONERY"):                 ("Chips, Chocolates & Snacks", "Lollies"),
    ("GROCERIES", "COOKING INGREDIENTS"):           ("Pantry", "Healthy Cooking"),
    ("GROCERIES", "DAIRY BEVERAGES"):               ("Drinks", "Flavoured Milk"),
    ("GROCERIES", "ETHNIC / GOURMET FOOD"):         ("Pantry", "Asian"),
    ("GROCERIES", "FLOUR SUGAR SALT"):              ("Pantry", "Sugar & Sweeteners"),
    ("GROCERIES", "FROZEN FOODS"):                  ("Frozen", "Convenience Meals"),
    ("GROCERIES", "HEALTH FOODS"):                  ("Pantry", "Healthy Snacks"),
    ("GROCERIES", "HOME BAKING"):                   ("Pantry", "Cake & Bread Mixes"),
    ("GROCERIES", "HONEY JAMS SPREADS"):            ("Pantry", "Honey"),
    ("GROCERIES", "HOUSEHOLD"):                     ("Cleaning & Laundry", ""),
    ("GROCERIES", "JUICES"):                        ("Drinks", "Plastic Juice Bottles"),
    ("GROCERIES", "LONG LIFE DAIRY"):               ("Drinks", "Long Life Milk"),
    ("GROCERIES", "MEAT POULTRY"):                  ("Meat & Seafood", ""),
    ("GROCERIES", "NOODLES RICE PASTA"):            ("Pantry", "Pasta"),
    ("GROCERIES", "OILS VINEGARS DRESSINGS"):       ("Pantry", "Oil"),
    ("GROCERIES", "PAPERGOODS"):                    ("Cleaning & Laundry", "Plastic Wraps & Bags"),
    ("GROCERIES", "PASTA SAUCES"):                  ("Pantry", "Recipe & Meal Bases"),
    ("GROCERIES", "PICKLES SAUCES CONDIMENTS"):     ("Pantry", "Pickles, Chutney & Relish"),
    ("GROCERIES", "POTATO SNACKS"):                 ("Chips, Chocolates & Snacks", "Chips Sharing"),
    ("GROCERIES", "SAVOURY BISCUITS"):              ("Chips, Chocolates & Snacks", "Crackers & Crispbreads"),
    ("GROCERIES", "SNACK FOODS"):                   ("Chips, Chocolates & Snacks", "Flavoured Snacks"),
    ("GROCERIES", "SOFT DRINKS"):                   ("Drinks", "Soft Drink Bottles"),
    ("GROCERIES", "SOUPS"):                         ("Pantry", "Soups"),
    ("GROCERIES", "SUGAR CONFECTIONERY"):           ("Chips, Chocolates & Snacks", "Lollies"),
    ("GROCERIES", "TEA"):                           ("Pantry", "Herbal"),
    ("GROCERIES", "WATER"):                         ("Drinks", "Still Water"),

    # FRESH CONVENIENCE
    ("FRESH CONVENIENCE", "CHILLED MEALS"):         ("Dairy, Eggs & Fridge", "Other Ready Meals"),
    ("FRESH CONVENIENCE", "CHILLED SNACKS"):        ("Dairy, Eggs & Fridge", "Grab & Go Snacks"),
    ("FRESH CONVENIENCE", "DAIRY"):                 ("Dairy, Eggs & Fridge", ""),
    ("FRESH CONVENIENCE", "DELI MEATS"):            ("Dairy, Eggs & Fridge", "Packaged Deli Meat"),
    ("FRESH CONVENIENCE", "EGGS"):                  ("Dairy, Eggs & Fridge", "Free Range Eggs"),
    ("FRESH CONVENIENCE", "FRESH PASTA"):           ("Dairy, Eggs & Fridge", "Fresh Pasta & Noodles"),
    ("FRESH CONVENIENCE", "JUICE"):                 ("Drinks", "Chilled Juice"),
    ("FRESH CONVENIENCE", "MILK"):                  ("Dairy, Eggs & Fridge", "Full Cream Milk"),
    ("FRESH CONVENIENCE", "SMALLGOODS"):            ("Dairy, Eggs & Fridge", "Packaged Deli Meat"),
    ("FRESH CONVENIENCE", "YOGHURT"):               ("Dairy, Eggs & Fridge", "Yoghurt Tubs"),

    # GENERAL MERCHANDISE
    ("GENERAL MERCHANDISE", "BATTERIES GLOBES"):    ("Home & Garden", "Batteries"),
    ("GENERAL MERCHANDISE", "CLEANING"):            ("Cleaning & Laundry", "Multipurpose Cleaners"),
    ("GENERAL MERCHANDISE", "HARDWARE"):            ("Home & Garden", "Tools & Accessories"),
    ("GENERAL MERCHANDISE", "HOMEWARES"):           ("Home & Garden", ""),
    ("GENERAL MERCHANDISE", "KITCHEN"):             ("Cleaning & Laundry", "Utensils & Gadgets"),
    ("GENERAL MERCHANDISE", "LAUNDRY"):             ("Cleaning & Laundry", "Laundry Liquid"),
    ("GENERAL MERCHANDISE", "PAPER"):               ("Cleaning & Laundry", "Plastic Wraps & Bags"),
    ("GENERAL MERCHANDISE", "PERSONAL CARE"):       ("Health & Beauty", ""),
    ("GENERAL MERCHANDISE", "PEST CONTROL"):        ("Cleaning & Laundry", "Crawling Insects"),
    ("GENERAL MERCHANDISE", "PET CARE"):            ("Pet", ""),
    ("GENERAL MERCHANDISE", "STATIONERY"):          ("Home & Garden", "Stationery"),

    # LIQUOR
    ("LIQUOR", "BEER"):                             ("Liquorland", "Full-Strength"),
    ("LIQUOR", "CIDER"):                            ("Liquorland", "Apple Cider"),
    ("LIQUOR", "PREMIXED"):                         ("Liquorland", "Bourbon Premix"),
    ("LIQUOR", "SPIRITS"):                          ("Liquorland", "Whisky"),
    ("LIQUOR", "WINE"):                             ("Liquorland", "Shiraz & Blends"),

    # FRUIT AND VEG
    ("FRUIT AND VEG", "FRUIT"):                     ("Fruit & Vegetables", "Tropical & Exotic Fruit"),
    ("FRUIT AND VEG", "HERBS"):                     ("Fruit & Vegetables", "Herbs & Chillies"),
    ("FRUIT AND VEG", "NUTS"):                      ("Fruit & Vegetables", "Other Nuts"),
    ("FRUIT AND VEG", "VEGETABLES"):                ("Fruit & Vegetables", "Other Vegetables"),

    # BAKERY
    ("PROPRIETARY BAKERY", "BREAD"):                ("Bakery", "Bread Loaves"),
    ("PROPRIETARY BAKERY", "CAKES"):                ("Bakery", "Sponge & Mud Cakes"),
    ("PROPRIETARY BAKERY", "PASTRIES"):             ("Bakery", "Pastries & Danishes"),
    ("BAKEHOUSE", "BREAD"):                         ("Bakery", "Bread Loaves"),
    ("BAKEHOUSE", "ROLLS"):                         ("Bakery", "Bread Rolls"),

    # DELI
    ("DELI SERVICE", "CHEESE"):                     ("Deli", "Fetta, Haloumi & Other"),
    ("DELI SERVICE", "DELI MEATS"):                 ("Deli", "Packaged Deli Meat"),
    ("DELI SERVICE", "SEAFOOD"):                    ("Meat & Seafood", "Deli Fish"),

    # SEAFOOD
    ("SEAFOOD SERVICE", "FISH"):                    ("Meat & Seafood", "Deli Fish"),
    ("SEAFOOD SERVICE", "SEAFOOD"):                 ("Meat & Seafood", "Prepacked Seafood"),

    # TOBACCO / FRONT OF STORE
    ("FRONT OF STORE", "TOBACCO"):                  ("Tobacco", "Cigarettes"),
    ("FRONT OF STORE", "CONFECTIONERY"):            ("Chips, Chocolates & Snacks", "Chocolate Bars"),
}

# Fallback: department-only mapping when category doesn't match
_WW_DEPT_FALLBACK: Dict[str, Tuple[str, str]] = {
    "GROCERIES":          ("Pantry", ""),
    "FRESH CONVENIENCE":  ("Dairy, Eggs & Fridge", ""),
    "GENERAL MERCHANDISE": ("Home & Garden", ""),
    "LIQUOR":             ("Liquorland", ""),
    "FRUIT AND VEG":      ("Fruit & Vegetables", ""),
    "PROPRIETARY BAKERY": ("Bakery", ""),
    "BAKEHOUSE":          ("Bakery", ""),
    "DELI SERVICE":       ("Deli", ""),
    "SEAFOOD SERVICE":    ("Meat & Seafood", ""),
    "FRONT OF STORE":     ("Home & Garden", ""),
    "NON TRADING":        ("", ""),
    "SERVICED DELICATESSEN": ("Deli", ""),
}


def map_woolworths(
    sap_department: Optional[str],
    sap_category: Optional[str],
    sap_segment: Optional[str] = None,  # pylint: disable=unused-argument
) -> Tuple[str, str]:
    """
    Map Woolworths SAP fields to (canonical_category, canonical_subcategory).
    Returns ("", "") if no mapping found.

    The ``sap_segment`` parameter is reserved for future segment-level
    refinement of the lookup; current implementations only key on
    (department, category). Callers pass it ahead of any rule-set
    upgrade so the signature stays stable.
    """
    dept = (sap_department or "").strip().upper()
    cat  = (sap_category  or "").strip().upper()

    # Exact dept+category match
    result = _WW_MAP.get((dept, cat))
    if result:
        return result

    # Department-only fallback
    result = _WW_DEPT_FALLBACK.get(dept)
    if result:
        return result

    return ("", "")


def map_coles(
    coles_category: Optional[str],
    coles_subcategory: Optional[str],
) -> Tuple[str, str]:
    """
    Coles categories ARE canonical — pass through as-is, just normalise whitespace.
    """
    cat = (coles_category    or "").strip()
    sub = (coles_subcategory or "").strip()
    return (cat, sub)
