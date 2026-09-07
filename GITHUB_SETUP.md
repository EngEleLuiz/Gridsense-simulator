# Setting Up the GridSense Simulator on GitHub

This guide walks you through publishing the GridSense Simulator
(Phase 1 & 2) to your GitHub account.

## Prerequisites

- A GitHub account (free at https://github.com)
- Git installed locally (`git --version`)
- SSH or HTTPS credentials configured for GitHub
  - (GitHub has moved away from password auth; use either a personal
    access token or SSH key)

## Step 1: Create an Empty Repository on GitHub

1. Go to https://github.com/new
2. Fill in the details:
   - **Repository name:** `gridsense-simulator` (or whatever you prefer)
   - **Description:** "Electrical Grid Digital Twin — Simulation Engine &
     Streaming Pipeline"
   - **Visibility:** Public (recommended, so you can share the code in
     your portfolio) or Private (if you prefer)
3. **Important:** Do NOT check "Initialize this repository with:"
   - Skip README, .gitignore, and license for now (we already have those)
4. Click "Create repository"

You'll see a page with instructions like:
```
…or push an existing repository from the command line
git remote add origin https://github.com/YOUR_USERNAME/gridsense-simulator.git
git branch -M main
git push -u origin main
```

Keep this page open for reference (or just use the automated script below).

## Step 2a: Automated Setup (Recommended)

From the project root directory:

```bash
bash scripts/init_github.sh YOUR_GITHUB_USERNAME gridsense-simulator
```

Replace `YOUR_GITHUB_USERNAME` with your actual GitHub username.

**Example:**
```bash
bash scripts/init_github.sh luizgarcia gridsense-simulator
```

The script will:
1. Initialize a git repository (if not already done)
2. Add all files (respecting `.gitignore`)
3. Create an initial commit with a descriptive message
4. Add the GitHub remote
5. Push everything to `main` branch

## Step 2b: Manual Setup (If You Prefer)

If you want to do it step by step instead:

```bash
# Navigate to the project root
cd gridsense-simulator

# Initialize git (if not already done)
git init

# Add all files
git add .

# Create initial commit
git commit -m "Initial commit: GridSense Simulator Phase 1 & 2

- Simulation engine (pandapower): IEEE test networks, synthetic profiles
- Kafka publisher for telemetry streaming
- Bronze-layer consumer: Kafka -> local Parquet landing zone
- Comprehensive unit tests (no external dependencies)
- Free, self-hosted Docker Compose stack (Kafka KRaft + Kafka UI)
- DuckDB queries for zero-cost analytics

Phase 1: Simulation engine runnable locally, no Kafka
Phase 2: Streaming pipeline with local Kafka + Parquet lake

Next phases: dbt (Silver/Gold), TimescaleDB, Grafana, ML models"

# Add the GitHub remote
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/gridsense-simulator.git

# Rename branch to 'main' (GitHub's default)
git branch -M main

# Push to GitHub
git push -u origin main
```

Replace `YOUR_GITHUB_USERNAME` with your actual username.

## Step 3: Verify

1. Go to https://github.com/YOUR_USERNAME/gridsense-simulator
2. You should see all the files:
   - `simulator/`, `ingestion/`, `docker-compose.yml`, `Makefile`, etc.
3. The commit message should be visible in the commit history

## Step 4: Configure GitHub (Optional but Recommended)

Once the repo is pushed, configure a few things on GitHub to make it
more professional:

### Add a .gitattributes file (optional)

Helps ensure consistent line endings across Windows/Mac/Linux:

```bash
echo "* text=auto" > .gitattributes
git add .gitattributes && git commit -m "Add .gitattributes"
git push
```

### Set branch protection (optional)

On GitHub:
1. Go to Settings → Branches
2. Click "Add rule"
3. Branch name pattern: `main`
4. Check "Require pull request reviews before merging" (enforce code review)
5. This is good practice even for solo projects (forces you to write
   commit messages and review your own PRs)

### Add Topics (optional)

On the repo page, click "About" (top right) and add topics like:
- `power-systems`
- `electrical-engineering`
- `data-engineering`
- `streaming`
- `kafka`
- `python`

This makes your repo discoverable.

## Step 5: Continue Development

To work on new features without affecting `main`:

```bash
# Create a feature branch
git checkout -b feature/phase-2-local-testing

# Make changes, commit as usual
git add .
git commit -m "Update: add Kafka publisher tests"

# Push the feature branch
git push -u origin feature/phase-2-local-testing

# On GitHub, open a Pull Request:
# - Go to your repo
# - GitHub will suggest "Compare & pull request"
# - Write a description, review your changes, merge
```

This keeps `main` clean and allows you to review changes before merging.

## Step 6: Add a License (Recommended)

GitHub makes it easy. On your repo page:
1. Click "Add file" → "Create new file"
2. Name: `LICENSE`
3. On the right, click "Choose a license template"
4. Pick one (for open-source, Apache 2.0 or MIT are popular)
5. Commit

For a data engineering portfolio project, MIT or Apache 2.0 are both
industry-standard and show you're open to collaboration.

## Troubleshooting

### "fatal: not a git repository"

```bash
# Make sure you're in the project root
cd gridsense-simulator
git init
```

### "Permission denied (publickey)"

You likely need to set up SSH keys for GitHub. See:
https://docs.github.com/en/authentication/connecting-to-github-with-ssh

Alternatively, use HTTPS with a personal access token:
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens

### "Already exists"

If you get an error like "remote origin already exists", reset it:

```bash
git remote remove origin
git remote add origin https://github.com/YOUR_USERNAME/gridsense-simulator.git
```

---

Once your repo is on GitHub, you're ready to:
1. Test Phase 2 locally (`make up`, `make produce`, `make consume-bronze`)
2. Commit and push any updates
3. Share the link in your portfolio or resume
4. Continue adding features (Phase 3: dbt, Phase 4: Grafana, etc.)

Good luck! 🚀
