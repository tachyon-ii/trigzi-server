"""
=============================================================================
Test:          Sibling repository presence check
Location:      trigzi-server/tests/test_sibling_repos.py
Description:   Verifies that the sibling repositories this server depends on
               are checked out alongside trigzi-server.

               All Trigzi repos are siblings under a common parent:

                   <parent>/
                     trigzi-server/      ← you are here
                     trigzi-common/      ← shared code (lib/, bin/)
                     trigzi-contracts/   ← locked contracts + eu14_bitmask.py
                     trigzi-off/         ← ingredient.db pipeline
                     trigzi-db/          ← gtin_cache.db pipeline

               There are two tiers of dependency:

               CODE DEPENDENCIES (server cannot start correctly without these)
               ─────────────────────────────────────────────────────────────
               trigzi-common
                 core/serialiser.py imports eu14_bitmask from trigzi-contracts/build/
                 utils/ modules are symlinked from trigzi-common/lib/ via linker.sh.
                 If trigzi-common is missing, run:
                     cd trigzi-server && ../trigzi-common/bin/linker.sh

               trigzi-contracts
                 core/serialiser.py locates trigzi-contracts/build/eu14_bitmask.py
                 using a path relative to its own __file__.  If this repo is absent,
                 the serialiser raises ImportError at startup.

               DATA PIPELINE DEPENDENCIES (server reads their output files at runtime)
               ────────────────────────────────────────────────────────────────────────
               trigzi-off
                 Builds ingredient.db (loaded by sqlite_store at startup).
                 Without it you cannot rebuild the taxonomy or update allergen/
                 sensitivity data.

               trigzi-db
                 Builds gtin_cache.db (primary product lookup) and content.db
                 (canonical ingredient descriptions).
                 Without it you cannot run the full scrape → enrich → ship pipeline.

               Run:
                   pytest tests/test_sibling_repos.py -v
=============================================================================
"""

import pytest
from pathlib import Path

# ── Locate the parent directory (one level above trigzi-server/) ──────────────
_SERVER_DIR = Path(__file__).resolve().parent.parent   # …/trigzi-server/
_PARENT_DIR = _SERVER_DIR.parent                        # …/<parent>/


# ── Helpers ───────────────────────────────────────────────────────────────────

def _repo_path(name: str) -> Path:
    return _PARENT_DIR / name


def _readme(name: str) -> Path:
    return _repo_path(name) / "README.md"


# ── Code dependency tests ─────────────────────────────────────────────────────

def test_trigzi_common_present():
    """
    trigzi-common must be checked out as a sibling of trigzi-server.

    It provides the shared lib/ modules (ingredient_parser, nutrition, gtin, …)
    that are symlinked into trigzi-server/utils/ via linker.sh, and the
    bin/linker.sh script itself.

    If this fails, clone the repo:
        cd <parent> && git clone <remote>/trigzi-common
    Then link it:
        cd trigzi-server && ../trigzi-common/bin/linker.sh
    """
    assert _readme("trigzi-common").exists(), (
        f"trigzi-common not found at {_repo_path('trigzi-common')}.\n"
        "Clone it alongside trigzi-server and run linker.sh."
    )


def test_trigzi_contracts_present():
    """
    trigzi-contracts must be checked out as a sibling of trigzi-server.

    core/serialiser.py imports eu14_bitmask from trigzi-contracts/build/ at
    startup.  The locked contract documents (contracts/gtin-cache-db.md etc.)
    are also required reading before modifying any shared schema.

    If this fails, clone the repo:
        cd <parent> && git clone <remote>/trigzi-contracts
    """
    assert _readme("trigzi-contracts").exists(), (
        f"trigzi-contracts not found at {_repo_path('trigzi-contracts')}.\n"
        "Clone it alongside trigzi-server."
    )


def test_trigzi_contracts_eu14_bitmask_importable():
    """
    trigzi-contracts/build/eu14_bitmask.py must exist and define all 14
    TRIGZI_ALLERGEN_* constants.

    This is the generated file that core/serialiser.py imports at startup.
    If the build/ directory is missing, run:
        cd trigzi-contracts && python3 scripts/gen_eu14.py
    """
    build_dir = _repo_path("trigzi-contracts") / "build"
    bitmask_py = build_dir / "eu14_bitmask.py"

    assert build_dir.exists(), (
        f"trigzi-contracts/build/ not found at {build_dir}.\n"
        "Run: cd trigzi-contracts && python3 scripts/gen_eu14.py"
    )
    assert bitmask_py.exists(), (
        f"eu14_bitmask.py not found at {bitmask_py}.\n"
        "Run: cd trigzi-contracts && python3 scripts/gen_eu14.py"
    )

    import importlib.util
    spec = importlib.util.spec_from_file_location("eu14_bitmask", bitmask_py)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    missing = [
        f"TRIGZI_ALLERGEN_{name}"
        for name in (
            "CELERY", "CRUSTACEANS", "EGGS", "FISH", "GLUTEN", "LUPIN",
            "MILK", "MOLLUSCS", "MUSTARD", "TREE_NUTS", "PEANUTS",
            "SESAME", "SOY", "SULPHITES",
        )
        if not hasattr(mod, f"TRIGZI_ALLERGEN_{name}")
    ]
    assert not missing, (
        f"eu14_bitmask.py is missing constants: {missing}\n"
        "Re-run: cd trigzi-contracts && python3 scripts/gen_eu14.py"
    )


def test_trigzi_common_linker_present():
    """
    trigzi-common/bin/linker.sh must exist.

    linker.sh is the canonical way to wire shared lib/ modules into a consumer
    repo.  Its absence means the common repo is partially cloned or corrupted.
    """
    linker = _repo_path("trigzi-common") / "bin" / "linker.sh"
    assert linker.exists(), (
        f"linker.sh not found at {linker}.\n"
        "The trigzi-common clone may be incomplete."
    )


# ── Data pipeline dependency tests ───────────────────────────────────────────
#
# These repos are not Python imports, but the server reads their output files
# at runtime (gtin_cache.db, ingredient.db, content.db).  A missing repo
# doesn't break the server import chain, but blocks any pipeline rebuild.
# Tests are marked xfail so CI stays green in minimal environments that only
# need to run the server, while still surfacing the gap as a warning.

@pytest.mark.xfail(reason="data pipeline dep — server runs without it, but pipeline rebuilds require it", strict=False)
def test_trigzi_off_present():
    """
    trigzi-off should be checked out as a sibling.

    It builds ingredient.db (the ingredient taxonomy, 2 423 canonicals,
    269 k aliases).  Without it you cannot rebuild ingredient.db after a
    taxonomy update.
    """
    assert _readme("trigzi-off").exists(), (
        f"trigzi-off not found at {_repo_path('trigzi-off')}."
    )


@pytest.mark.xfail(reason="data pipeline dep — server runs without it, but pipeline rebuilds require it", strict=False)
def test_trigzi_db_present():
    """
    trigzi-db should be checked out as a sibling.

    It runs the merge → enrich → write pipeline that produces gtin_cache.db
    and content.db, and houses the MySQL schema (store/schema.sql).
    """
    assert _readme("trigzi-db").exists(), (
        f"trigzi-db not found at {_repo_path('trigzi-db')}."
    )
