#!/bin/sh
# Entrypoint of the compute-collector image. Credentials only ever reach the process from Docker secrets mounted read-only under
# /run/secrets; nothing is copied into the image, a volume or the mounted repo tree.
#   claude_credentials  the CLI login file (Windows: %USERPROFILE%\.claude\.credentials.json). The CLI writes into its config
#                       directory, so the read-only secret is copied into a private tmpfs directory (CLAUDE_CONFIG_DIR).
#   govinfo_key, fec_data_gov  single-line key files, exported as GOVINFO_KEY / FEC_API_KEY (the names the collectors read first).
# A secret that is not mounted is simply absent; the collector then fails on its own missing key, loudly.
set -eu
secrets="${SECRETS_DIR:-/run/secrets}"   # SECRETS_DIR exists for the test; the container never sets it
mkdir -p "${HOME:-/tmp/home}"

if [ -f "$secrets/claude_credentials" ]; then
    : "${CLAUDE_CONFIG_DIR:=/tmp/claude}"
    export CLAUDE_CONFIG_DIR
    # umask only inside the subshell: the collector must not inherit 077 and write its outputs owner-only
    ( umask 077; mkdir -p "$CLAUDE_CONFIG_DIR"; cp "$secrets/claude_credentials" "$CLAUDE_CONFIG_DIR/.credentials.json" )
fi

file_env() {   # file_env VAR secret_name
    if [ -f "$secrets/$2" ]; then
        value=$(cat "$secrets/$2")
        export "$1=$value"
    fi
}
file_env GOVINFO_KEY govinfo_key
file_env FEC_API_KEY fec_data_gov

exec "$@"
