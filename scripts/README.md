# Scripts

Utility scripts for development and deployment.

## `init_github.sh`

Initializes a git repository and pushes it to GitHub in one command.

### Usage

```bash
bash scripts/init_github.sh <github_username> <repository_name>
```

### Example

```bash
bash scripts/init_github.sh luizgarcia gridsense-simulator
```

### What it does

1. Initializes git in the current directory
2. Stages all files (respecting `.gitignore`)
3. Creates an initial commit
4. Adds the GitHub remote
5. Pushes to GitHub on the `main` branch

### Prerequisites

- Git installed
- GitHub account
- Empty repository created on GitHub (https://github.com/new)
- SSH or HTTPS credentials configured for GitHub

For detailed instructions, see [`GITHUB_SETUP.md`](../GITHUB_SETUP.md) in
the project root.
