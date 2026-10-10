#!/bin/bash
# Enables the repository's git hooks (.githooks/pre-commit: tests + build of the DietPi installer on every commit).
set -e
cd "$(git rev-parse --show-toplevel)"
chmod +x .githooks/* scripts/*.sh scripts/*.py 2>/dev/null || true
git config core.hooksPath .githooks
echo "Hooks enabled: git commit now runs the tests and builds dist/."
