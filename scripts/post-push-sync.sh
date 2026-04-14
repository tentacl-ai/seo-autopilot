#!/bin/bash
# Post-Push Sync: Aktualisiert GitHub Description nach jedem Push.
# Wird vom post-push Git Hook aufgerufen (oder manuell).
#
# Voraussetzungen:
# - gh CLI authentifiziert (gh auth status)
# - Ausfuehrung im Repo-Root

set -e

cd "$(git rev-parse --show-toplevel)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

VERSION=$(grep -oP 'version="\K[^"]+' setup.py 2>/dev/null || echo "?")
MODULES=$(find seo_autopilot/analyzers/ -name "*.py" ! -name "__init__*" ! -path "*__pycache__*" 2>/dev/null | wc -l)

echo -e "${YELLOW}Post-Push Sync: v$VERSION, $MODULES Module${NC}"

# === 1. GitHub Description ===
if command -v gh &>/dev/null && gh auth status &>/dev/null; then
    DESC="Multi-tenant SEO automation — ${MODULES} analyzers, 80+ checks, Playwright JS rendering, llms.txt/ai.txt validation, IndexNow, GEO audit. Real crawler, GSC, PageSpeed, AI-powered fixes."
    gh repo edit tentacl-ai/seo-autopilot \
        --description "$DESC" \
        --homepage "https://tentacl.ai/seo-autopilot/" 2>/dev/null
    echo -e "${GREEN}OK${NC} GitHub Description aktualisiert"
else
    echo -e "${RED}--${NC} GitHub: gh CLI nicht authentifiziert"
fi

# === 2. Reminder fuer manuelle Schritte ===
echo ""
echo -e "${YELLOW}Manuelle Schritte pruefen:${NC}"
echo "  [ ] Landing Page:  /opt/apps/landing/frontend/dist/seo-autopilot/index.html"
echo "  [ ] llms.txt:      /opt/apps/landing/frontend/dist/llms.txt"
echo "  [ ] seo-check:     /var/www/seo-check/index.html"
echo "  [ ] PyPI:          python -m build && twine upload dist/*"
echo "  [ ] Container:     sudo docker compose build && up -d seo-autopilot"
echo ""
