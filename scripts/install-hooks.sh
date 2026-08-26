#!/usr/bin/env sh
# Install the pre-push secret gate. Git does not ship hooks with a clone, so
# without this the README's "installed as a pre-push hook" is true only on the
# machine where it was set up — which is exactly the kind of claim this project
# is supposed to not make.
set -e
root=$(git rev-parse --show-toplevel)
mkdir -p "$root/.git/hooks"
cat > "$root/.git/hooks/pre-push" <<'HOOK'
#!/usr/bin/env sh
exec "$(git rev-parse --show-toplevel)/scripts/scan-secrets.sh"
HOOK
chmod +x "$root/.git/hooks/pre-push"
echo "pre-push hook installed -> $root/.git/hooks/pre-push"
