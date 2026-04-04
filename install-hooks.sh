#!/bin/sh
set -e
cp .git/hooks/pre-commit .git/hooks/pre-commit.bak 2>/dev/null || true
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/sh
set -e
uv run ruff check .
uv run ruff format --check .
EOF
chmod +x .git/hooks/pre-commit
echo "pre-commit hook installed"
