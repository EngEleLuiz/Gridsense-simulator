#!/bin/bash
# Script to initialize the GridSense Simulator repo and push to GitHub.
# 
# Prerequisites:
#   1. You have a GitHub account
#   2. You've created an empty repository on GitHub (no README, no license, no .gitignore)
#   3. You have git installed locally
#   4. You have SSH or HTTPS credentials set up for GitHub
#
# Usage:
#   ./scripts/init_github.sh <github_username> <repository_name>
#
# Example:
#   ./scripts/init_github.sh luizgarcia gridsense-simulator

set -e  # Exit on error

if [ $# -ne 2 ]; then
    echo "Usage: $0 <github_username> <repository_name>"
    echo ""
    echo "Example:"
    echo "  $0 luizgarcia gridsense-simulator"
    exit 1
fi

GITHUB_USER=$1
REPO_NAME=$2
REPO_URL="https://github.com/${GITHUB_USER}/${REPO_NAME}.git"

echo "════════════════════════════════════════════════════════════"
echo "GridSense Simulator — GitHub Repository Initialization"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "GitHub user:  $GITHUB_USER"
echo "Repository:   $REPO_NAME"
echo "URL:          $REPO_URL"
echo ""

# Check if git is initialized
if [ -d .git ]; then
    echo "✓ Git repository already initialized."
else
    echo "→ Initializing git repository..."
    git init
fi

# Add all files (respecting .gitignore)
echo "→ Adding files to staging area..."
git add .

# Create initial commit
echo "→ Creating initial commit..."
git commit -m "Initial commit: GridSense Simulator Phase 1 & 2

- Simulation engine (pandapower): IEEE test networks, synthetic load/renewable profiles
- Kafka publisher for telemetry streaming
- Bronze-layer consumer: Kafka -> local Parquet landing zone
- Comprehensive unit tests (no external dependencies)
- Free, self-hosted Docker Compose stack (Kafka KRaft + Kafka UI)
- DuckDB queries for zero-cost analytics

Phase 1: Simulation engine runnable locally, no Kafka
Phase 2: Streaming pipeline with local Kafka + Parquet lake

Next phases: dbt (Silver/Gold), TimescaleDB, Grafana, ML models"

# Set the remote
echo "→ Setting remote origin..."
git remote add origin "$REPO_URL" || git remote set-url origin "$REPO_URL"

# Push to GitHub
echo "→ Pushing to GitHub (branch: main)..."
git branch -M main
git push -u origin main

echo ""
echo "════════════════════════════════════════════════════════════"
echo "✓ Repository initialized and pushed to GitHub!"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo ""
echo "  1. Verify on GitHub:"
echo "     https://github.com/${GITHUB_USER}/${REPO_NAME}"
echo ""
echo "  2. Clone it elsewhere (if needed):"
echo "     git clone https://github.com/${GITHUB_USER}/${REPO_NAME}.git"
echo ""
echo "  3. Continue development:"
echo "     git checkout -b feature/your-feature-name"
echo "     # ... make changes ..."
echo "     git add . && git commit -m 'Your commit message'"
echo "     git push -u origin feature/your-feature-name"
echo "     # ... open a Pull Request on GitHub"
echo ""
