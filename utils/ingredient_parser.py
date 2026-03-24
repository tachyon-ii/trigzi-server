#!/usr/bin/env python3
"""
ingredient_parser.py — extract a flat ordered list of ingredient tokens from a raw string.

parse_ingredients(raw) -> List[str]

Returns a deduplicated, order-preserving flat list of canonical ingredient tokens.
"""

import re
from typing import List, Set, Tuple, Optional
from html.parser import HTMLParser
from pathlib import Path


# ---------------------------------------------------------------------------
# Vocabulary — loaded once at module import
# ---------------------------------------------------------------------------

def _load_vocab() -> Set[str]:
    p = Path(__file__).parent.parent / "data" / "clean_words.txt"
    if p.exists():
        return set(p.read_text().splitlines())
    return set()

_VOCAB = _load_vocab()


# ---------------------------------------------------------------------------
# OCR merge/split detector
# ---------------------------------------------------------------------------

def _longest_match_split(token: str, vocab: set, min_len: int = 3) -> Optional[List[str]]:
    """
    Split token into a sequence of vocab words using longest-match-first (DP).
    Returns list of words if a complete cover is found, None otherwise.
    """
    n = len(token)
    dp: List[Optional[List[str]]] = [None] * (n + 1)
    dp[0] = []
    for i in range(n):
        if dp[i] is None:
            continue
        for j in range(n, i + min_len - 1, -1):
            chunk = token[i:j]
            if chunk in vocab and dp[j] is None:
                dp[j] = dp[i] + [chunk]
    return dp[n]


def _fix_token_ocr(token: str) -> List[str]:
    """
    Fix OCR artefacts within a single token using two passes:

    1. Digram join — slide over space-separated words, join adjacent pair if
       the concatenation is in vocab. If no digrams fire, return original token
       unchanged (preserves valid phrases like "tree nuts", "acidity regulator").
       If a digram fires, recurse on the result to handle chains.

       "n atural flavour" → try "natural" → in vocab → recurse ["natural flavour"]
                         → try "naturalflavour" → not in vocab → ["natural", "flavour"]
       "colour 77891"    → try "colour77891" → not in vocab → return ["colour 77891"]
       "anti oxidant"    → try "antioxidant" → in vocab → ["antioxidant"]

    2. Conjoint split — if token has no spaces and is not in vocab, try DP
       split into vocab words.
       "soylecithin"    → ["soy", "lecithin"]
    """
    if not _VOCAB or not token:
        return [token]

    # Pass 1: digram join (only applies if token contains spaces)
    if ' ' in token:
        words = token.split()
        for i in range(len(words) - 1):
            digram = words[i] + words[i + 1]
            if digram in _VOCAB:
                # Digram fired — rebuild token with join, recurse
                merged = words[:i] + [digram] + words[i + 2:]
                return _fix_token_ocr(' '.join(merged))
        # No digrams fired — return original token as-is (it's a valid phrase)
        return [token]

    # Pass 2: conjoint split (no spaces)
    if token not in _VOCAB and len(token) >= 6:
        parts = _longest_match_split(token, _VOCAB)
        if parts and len(parts) >= 2:
            return parts

    return [token]


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

class _MLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []

    def handle_data(self, d):
        self.fed.append(d)

    def get_data(self):
        return ' '.join(self.fed)


def _strip_html(raw: str) -> str:
    s = _MLStripper()
    s.feed(raw)
    return s.get_data()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ABBREVS = {
    'denat', 'ext', 'min', 'max', 'approx', 'avg', 'std', 'vol', 'wt',
    'no', 'vs', 'incl', 'excl', 'est',
}

_DISCLAIMER_RE = re.compile(
    r'^\s*(contains\b|may contain\b|\+/-\s*may contain\b|allergen\b|'
    r'refer to\b|please\b|for more\b|for full\b|'
    r'ingredients\s+and\s+percentages\b|'
    r'and\s+percentages\s+may\s+vary\b|'
    r'percentages\s+may\s+vary\b|'
    r'store\s+in\b|dry\s+place\b|cool\s+dry\b|it.s\s+best\b|check\s+the\b|'
    r'this\s+meal\b|this\s+product\b|manufactured\s+in\b|'
    r'packed\s+in\b|produced\s+in\b|made\s+in\b|'
    r'msc\s+certified\b|certified\s+organic\s*$|organic\s*$|'
    r'ingredients\s*$|f\.i\.l\b|'
    r'visit\s+our\b|see\s+our\b|not\s+suitable\b)',
    re.IGNORECASE
)

# Leading-word prefixes that are qualifiers, not ingredients
_LEADING_QUALIFIER_RE = re.compile(
    r'^(from|including|derived\s+from|contains?\b|containing\b|'
    r'added\b|with\b|as\b|and\b|or\b|of\b|the\b|to\b|an?\b)\s+',
    re.IGNORECASE
)

# Single tokens that are pure stopwords even without context
_SHORT_ABBREVS = {'ci', 'hv', 'dl', 'pp', 'fe', 'ca', 'na', 'zn', 'cu', 'mn'}

_STOPWORD_TOKENS = {'to', 'of', 'the', 'and', 'or', 'as', 'an', 'a',
                    'equiv', 'equivalent', 'supplement', 'place', 'taste',
                    'cool', 'dry', 'dark', 'including', 'contain', 'contains',
                    'product', 'range', 'more', 'each', 'per', 'total',
                    'added', 'derived', 'active', 'processed',
                    'contents', 'serving', 'roasted', 'ground', 'garnish',
                    'composition', 'complete', 'accurate', 'however',
                    'interior', 'farming', 'swatches', 'biodegradable',
                    'france', 'plastic', 'brands', 'hundreds', 'thousands'}

# Brand/company names and internal codes to discard entirely
_BRAND_JUNK_RE = re.compile(
    r'^(colgatepalmolive|fablaundry|sardwonder|coldpower|colgate|unilever|'
    r'rbeuroinfo|crunchie|panorama|zartin|tioxidant|bb-12tm|ngredients|'
    r'eacute|egrave|ograve|ndash|nbsp|ntilde|apos|quot)$',
    re.IGNORECASE
)

_CATEGORY_LABEL_RE = re.compile(
    r'\b(preservatives?|emulsifiers?|antioxidants?|colours?|colors?|'
    r'thickeners?|stabilisers?|stabilizers?|acidity\s+regulators?|'
    r'humectants?|flavou?r\s+enhancers?|raising\s+agents?|'
    r'firming\s+agents?|bulking\s+agents?|anti-?caking\s+agents?|'
    r'glazing\s+agents?|sequestrants?|flour\s+treatment\s+agents?)'
    r'\s+(?!\()',
    re.IGNORECASE
)

_STRIP_WORDS_RE = re.compile(
    r'\b(organic|certified|organically|produced|ingredient|ingredients|'
    r'raw|dried|fresh|whole|pure|refined|processed|edible|'
    r'supplement|equiv|equivalent)\b',
    re.IGNORECASE
)

# Stray punctuation / single non-alphanumeric tokens (including Unicode)
_PUNCT_ONLY_RE = re.compile(r'^[^a-z0-9\-]+$')


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------

def _preprocess(raw: str) -> str:
    if re.search(r'<(div|p)\b', raw, re.IGNORECASE):
        return ''

    # Escape & before HTML stripping
    raw = raw.replace('&', '\x00AMP\x00')
    raw = _strip_html(raw)
    raw = raw.replace('\x00AMP\x00', '&')

    # Strip HTML entities: &eacute; → e, &egrave; → e, &nbsp; → space etc.
    raw = re.sub(r'&[a-z]{2,8};', ' ', raw)

    # Normalise ß → beta, non-breaking space → space
    raw = raw.replace('ß', 'beta').replace('\xa0', ' ').replace('\u00df', 'beta')

    # Normalise vit-bN / vit.bN / vit bN → bN  (vit-b1, vit-b12, vit-b6 etc.)
    raw = re.sub(r'\bvit[\s.\-]*(b\d{1,2}|c|d\d?|e|k\d?)\b', r'\1', raw, flags=re.IGNORECASE)

    # Strip Unicode list bullets and marker symbols
    raw = re.sub(r'[•·–—®°†‡§\u00ae\u00b7\u2013\u2014\u2022\u2020\u2021\u00a7]', ' ', raw)

    # ? as delimiter (allergen marker in some sources: "soy??lecithin")
    raw = re.sub(r'\?+', ',', raw)

    # All bracket styles → comma (sub-ingredient delimiters)
    raw = raw.replace('[', ',').replace(']', ',')
    raw = raw.replace('{', ',').replace('}', ',')
    raw = raw.replace('[', '(').replace(']', ')')

    # INGREDIENTS: prefix
    raw = re.sub(r'^INGREDIENTS\s*:\s*', '', raw, flags=re.IGNORECASE)

    # Newlines → comma
    raw = re.sub(r'\n+', ', ', raw)

    # Collapse whitespace
    raw = re.sub(r'  +', ' ', raw)

    # INCI bilingual water
    raw = re.sub(r'\baqua\s*/\s*water\s*/\s*eau\b', 'water', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\baqua\s*/\s*water\b',            'water', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\baqua\b',                         'water', raw, flags=re.IGNORECASE)

    # Category labels: insert comma before bare name/code
    raw = _CATEGORY_LABEL_RE.sub(r'\1, ', raw)

    # &/or and and/or → comma
    raw = re.sub(r'\s*&/or\s*',   ', ', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s+and/or\s+', ', ', raw, flags=re.IGNORECASE)

    # Strip qualifier words
    raw = _STRIP_WORDS_RE.sub(' ', raw)
    raw = re.sub(r'  +', ' ', raw)

    # +/- → comma (allergen marker used as delimiter)
    raw = re.sub(r'\s*\+/-\s*', ', ', raw)

    # Period as delimiter: "word. Word" or "word.word" where both sides are alpha
    # but NOT inside chemical names like "adenosine-5'-monophosphate"
    raw = re.sub(r'([a-z])\.\s+([a-z])', r'\1, \2', raw, flags=re.IGNORECASE)
    raw = re.sub(r'([a-z])\.([a-z])', r'\1, \2', raw, flags=re.IGNORECASE)

    # / used as delimiter between alternatives
    raw = re.sub(r'\s*/\s*', ', ', raw)

    # Standalone and / or / from as delimiters — but not when preceded by hyphen (chemical names)
    # "water and chicken" → "water, chicken"
    # "mono- and di-glycerides" → preserved
    raw = re.sub(r'(?<!-)\s+(and|or|from)\s+(?!-)', ', ', raw, flags=re.IGNORECASE)

    # E-number prefix: e635 → 635
    raw = re.sub(r'\be(\d{3,4}\w*)\b', r'\1', raw, flags=re.IGNORECASE)

    # Remaining & → comma
    raw = raw.replace('&', ', ')

    return raw.strip()


# ---------------------------------------------------------------------------
# Token cleaning
# ---------------------------------------------------------------------------

def _clean_token(tok: str) -> str:
    if not tok:
        return ''

    tok = tok.replace('*', '')

    # Strip Unicode bullets and punctuation often found in ingredient lists
    tok = re.sub(r'^[\s\u00ae\u00b7\u2013\u2014\u2022\u00b0•·–—®°]+', '', tok)

    # Strip leading ASCII noise
    tok = re.sub(r'^[\s;,.<>()\[\]^%\-#+@!]+', '', tok)

    # Strip any trailing non-alphanumeric chars universally — guaranteed clean token end
    while tok and not tok[-1].isalnum():
        tok = tok[:-1]

    # Strip percentage declarations
    tok = re.sub(r'\s*[\d.]+\s*%.*$', '', tok).strip()

    # Strip leading qualifier words: "from milk" → "milk", "including chicken" → "chicken"
    for _ in range(3):
        new = _LEADING_QUALIFIER_RE.sub('', tok).strip()
        if new == tok:
            break
        tok = new

    # Trailing dot — keep abbreviations
    if tok.endswith('.'):
        last = tok.rsplit(None, 1)[-1][:-1].lower()
        if len(last) > 1 and last not in _ABBREVS:
            tok = tok[:-1]

    # Strip leading dosage prefix: "25g fat" → "fat", "25mgzinc" → "zinc"
    # Requires at least one digit before unit
    tok = re.sub(r'^\d[\d.]*\s*(?:mg|mcg|iu|g|ml|kg|ug|µg)\s*', '', tok).strip()
    # Strip bare mg/mcg prefix fusions: "mgzinc" → "zinc", "mcgvitamin" → "vitamin"
    tok = re.sub(r'^(?:mg|mcg)\s*', '', tok).strip()
    if not tok:
        return ''

    tok = tok.strip().lower()

    if not tok:                             return ''
    if len(tok) < 2:                        return ''
    # Drop 2-char tokens unless vitamin code (b6, d3), known abbrev, or known vocab word
    if len(tok) == 2 and not re.match(r'^[a-z]\d+$', tok) and tok not in _VOCAB and tok not in _SHORT_ABBREVS: return ''
    # Drop short tokens (3-5 chars) not in vocab, not additive/CI codes, not vitamin codes, not abbrevs
    if (len(tok) <= 5
            and tok not in _VOCAB
            and not re.fullmatch(r'\d{3,6}[a-z]?', tok)
            and not re.match(r'^[a-z]\d+$', tok)
            and tok not in _SHORT_ABBREVS):
        return ''
    if _PUNCT_ONLY_RE.match(tok):           return ''
    if tok in _STOPWORD_TOKENS:             return ''
    if _BRAND_JUNK_RE.match(tok):           return ''
    if _DISCLAIMER_RE.match(tok):           return ''
    if len(tok) > 80:                       return ''

    # Drop internal product/batch codes: g853331, d166390, z282167, c199905
    if re.fullmatch(r'[a-z]\d{5,}', tok):              return ''

    # Discard URLs
    if re.search(r'www\.|\.com|\.au|\.nz', tok):  return ''

    # Discard leading-hyphen chemical fragments: "-monophosphate", "-butyl"
    if tok.startswith('-'):                 return ''

    # Drop dosage fragments: "0g", "0il", "0.5mg", "100mgzinc"
    # Exempt additive codes (3-4 digits) and CI colour numbers (5-6 digits)
    if re.fullmatch(r'[\d.]+[a-z]{0,4}', tok) and not re.fullmatch(r'\d{3,6}[a-z]?', tok):
        return ''

    # Drop pure numeric fragments — keep additive codes (3-4 digits) and CI numbers (5-6 digits)
    if re.fullmatch(r'[\d\s.]+', tok) and not re.fullmatch(r'\d{3,6}[a-z]?', tok):
        return ''

    return tok


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_ingredients(raw: str) -> List[str]:
    """
    Parse a raw ingredient string into a flat, ordered, deduplicated token list.

    "Milk Chocolate (34%) [Sugar, Cocoa, Emulsifier (471)], Water, Salt"
    → ["milk chocolate", "sugar", "cocoa", "emulsifier", "471", "water", "salt"]
    """
    tokens, _ = _parse_with_unknowns(raw)
    return tokens


def parse_ingredients_debug(raw: str) -> Tuple[List[str], List[str]]:
    """
    Like parse_ingredients but also returns unknown tokens.
    Returns (tokens, unknowns) where unknowns are tokens that didn't
    pass vocab/additive-code validation — useful for corpus analysis.
    """
    return _parse_with_unknowns(raw)


def _extract_codes_from_string(raw: str) -> Tuple[List[str], List[str], str]:
    """
    First-pass extraction on the raw string before any other processing.
    Greedily extracts all numeric codes, replacing them with spaces.

    CI colour numbers:  5-6 digit standalone  → colour_codes list
    Additive codes:     3-4 digits + optional a-f suffix → additive_codes list

    Letter suffix range is a-f only (161a Flavoxanthin through 161f Rhodoxanthin).
    This avoids consuming word letters like the 's' in '471soy'.

    Returns (colour_codes, additive_codes, cleaned_string)
    """
    colour_codes   = []
    additive_codes = []

    # CI colour numbers first (5-6 digits, word boundary)
    for m in re.finditer(r'\b(\d{5,6})\b', raw):
        colour_codes.append(m.group(1))
    raw = re.sub(r'\b\d{5,6}\b', ' ', raw)

    # Additive codes (3-4 digits + optional a-f suffix, not preceded by digit)
    # Use (?<!\d) and (?!\d) since \b doesn't fire between digit and letter
    for m in re.finditer(r'(?<!\d)(\d{3,4}[a-f]?)(?!\d)', raw):
        additive_codes.append(m.group(1))
    raw = re.sub(r'(?<!\d)\d{3,4}[a-f]?(?!\d)', ' ', raw)

    raw = re.sub(r' {2,}', ' ', raw).strip()
    return colour_codes, additive_codes, raw


def _parse_with_unknowns(raw: str) -> Tuple[List[str], List[str]]:
    if not raw:
        return [], []

    # Step 1: extract all numeric codes from raw string first
    colour_codes, additive_codes, raw_stripped = _extract_codes_from_string(raw)

    # Step 2: preprocess the code-stripped string
    preprocessed = _preprocess(raw_stripped)
    if not preprocessed and not colour_codes and not additive_codes:
        return [], []

    seen: Set[str] = set()
    result: List[str] = []
    unknowns: List[str] = []

    def _emit(tok: str) -> None:
        if tok and tok not in seen:
            seen.add(tok)
            result.append(tok)

    def _is_known(tok: str) -> bool:
        if tok in _VOCAB:                           return True
        if tok in _SHORT_ABBREVS:                   return True
        if re.fullmatch(r'\d{3,4}[a-f]?', tok):    return True  # additive code
        if re.fullmatch(r'\d{5,6}', tok):           return True  # CI colour index
        if re.match(r'^[a-z]\d+$', tok):            return True  # vitamin code b6, d3
        return False

    # Emit all extracted codes first
    for code in colour_codes + additive_codes:
        _emit(code)

    if not preprocessed:
        return result, unknowns

    # OCR fix: "Eu,calyptus" → "Eucalyptus"
    preprocessed = re.sub(r'(?<=[a-zA-Z]),(?=[a-z])', '', preprocessed)

    # Flatten all bracket types
    preprocessed = preprocessed.replace('(', ',').replace(')', ',')

    # Split on all delimiters
    raw_tokens = re.split(r'[,;:]+', preprocessed)

    for tok in raw_tokens:
        cleaned = _clean_token(tok)
        if not cleaned:
            continue

        for fixed in _fix_token_ocr(cleaned):
            if not fixed:
                continue

            _emit(fixed)

            # Multi-word phrase → also emit component words
            if ' ' in fixed:
                for word in fixed.split():
                    word = _clean_token(word)
                    if word:
                        _emit(word)

            # Track unknowns
            if not _is_known(fixed) and ' ' not in fixed:
                unknowns.append(fixed)

    return result, unknowns
