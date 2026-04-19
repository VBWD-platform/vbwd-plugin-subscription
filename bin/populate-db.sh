#!/bin/bash
# Populate Subscription Database
# ===============================
# Seeds demo tarif plans, categories, and subscriptions.
#
# Usage:
#   ./plugins/subscription/bin/populate-db.sh
#
# Requirements:
#   - docker compose running with api service
#   - PostgreSQL database running and migrated

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Subscription Database Population      ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

if ! docker compose ps 2>/dev/null | grep -q "api.*Up"; then
    echo -e "${RED}✗ Error: api service is not running${NC}"
    echo "  Start with: make up"
    exit 1
fi

echo -e "${YELLOW}Populating subscription demo data...${NC}"
echo ""

docker compose exec -T api python /app/plugins/subscription/bin/run_populate.py

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✓ Subscription demo data populated${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}✗ Failed to populate subscription data${NC}"
    exit 1
fi
