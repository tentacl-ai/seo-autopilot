#!/bin/bash
# Prueft ob Version, Test-Count und Modul-Anzahl in allen Dateien synchron sind.
# Wird von pre-commit Hook UND GitHub CI aufgerufen.
#
# Exit 0 = alles synchron
# Exit 1 = Abweichung gefunden

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
ERRORS=0

# === 1. Version synchron? ===
V_SETUP=$(grep -oP 'version="\K[^"]+' setup.py 2>/dev/null || echo "?")
V_INIT=$(grep -oP '__version__ = "\K[^"]+' seo_autopilot/__init__.py 2>/dev/null || echo "?")
V_API=$(grep -oP 'version="\K[^"]+' seo_autopilot/api/main.py 2>/dev/null | head -1 || echo "?")
V_HEALTH=$(grep -oP '"version": "\K[^"]+' seo_autopilot/api/main.py 2>/dev/null || echo "?")

if [ "$V_SETUP" = "$V_INIT" ] && [ "$V_INIT" = "$V_API" ] && [ "$V_API" = "$V_HEALTH" ]; then
    echo -e "${GREEN}OK${NC} Version: $V_SETUP (setup.py, __init__.py, api/main.py x2)"
else
    echo -e "${RED}FEHLER${NC} Version nicht synchron:"
    echo "  setup.py:      $V_SETUP"
    echo "  __init__.py:    $V_INIT"
    echo "  api/main.py:    $V_API"
    echo "  /api/health:    $V_HEALTH"
    ERRORS=$((ERRORS + 1))
fi

# === 2. CHANGELOG hat aktuelle Version? ===
CL_VERSION=$(grep -oP '## \[\K[^\]]+' CHANGELOG.md 2>/dev/null | head -1 || echo "?")
if [ "$CL_VERSION" = "$V_SETUP" ]; then
    echo -e "${GREEN}OK${NC} CHANGELOG: neuester Eintrag ist v$CL_VERSION"
else
    echo -e "${RED}FEHLER${NC} CHANGELOG neuester Eintrag ist v$CL_VERSION, aber setup.py hat v$V_SETUP"
    ERRORS=$((ERRORS + 1))
fi

# === 3. Analyzer-Module zaehlen ===
MODULE_COUNT=$(find seo_autopilot/analyzers/ -name "*.py" ! -name "__init__*" ! -path "*__pycache__*" 2>/dev/null | wc -l)
README_MODULES=$(grep -oP '\b(\d+) analysis dimensions' README.md 2>/dev/null | grep -oP '\d+' | head -1 || echo "?")

if [ "$MODULE_COUNT" = "$README_MODULES" ]; then
    echo -e "${GREEN}OK${NC} Module: $MODULE_COUNT (Code = README)"
else
    echo -e "${RED}FEHLER${NC} $MODULE_COUNT Analyzer-Dateien, aber README sagt $README_MODULES dimensions"
    ERRORS=$((ERRORS + 1))
fi

# === 4. Tests zaehlen (optional, nur wenn pytest verfuegbar) ===
if command -v python3 &>/dev/null && python3 -c "import pytest" 2>/dev/null; then
    TEST_COUNT=$(python3 -m pytest tests/ --collect-only -q 2>/dev/null | tail -1 | grep -oP '\d+' | head -1 || echo "?")
    README_TESTS=$(grep -oP '\b(\d+) tests' README.md 2>/dev/null | grep -oP '\d+' | head -1 || echo "?")

    if [ "$TEST_COUNT" = "$README_TESTS" ]; then
        echo -e "${GREEN}OK${NC} Tests: $TEST_COUNT (Code = README)"
    elif [ "$TEST_COUNT" != "?" ]; then
        echo -e "${RED}FEHLER${NC} $TEST_COUNT Tests gefunden, aber README sagt $README_TESTS"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo -e "${GREEN}--${NC} Tests: uebersprungen (pytest nicht verfuegbar)"
fi

# === 5. Black Formatting? ===
if command -v black &>/dev/null; then
    if black --check --quiet seo_autopilot/ 2>/dev/null; then
        echo -e "${GREEN}OK${NC} Black: alle Dateien korrekt formatiert"
    else
        echo -e "${RED}FEHLER${NC} Black: Dateien nicht formatiert. Fuehre 'black seo_autopilot/' aus."
        ERRORS=$((ERRORS + 1))
    fi
else
    echo -e "${GREEN}--${NC} Black: uebersprungen (nicht installiert)"
fi

# === Ergebnis ===
echo ""
if [ $ERRORS -gt 0 ]; then
    echo -e "${RED}$ERRORS Sync-Fehler gefunden. Bitte vor dem Commit fixen.${NC}"
    exit 1
else
    echo -e "${GREEN}Alles synchron.${NC}"
    exit 0
fi
