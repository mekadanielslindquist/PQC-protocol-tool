#!/bin/bash
set -e

CRYPTO_DIR="/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto"
MARKER="${CRYPTO_DIR}/.generated.ok"

# Generate crypto materials if not already present. Keyed off a completion
# marker, not just "does the directory look non-empty" - a partially
# populated CRYPTO_DIR (e.g. one org's material present, another missing
# or left over from a different generation run) used to look "non-empty"
# to the old check and get skipped instead of regenerated. That's the
# most likely way this project ended up with two different self-signed
# CAs both named tlsca.example.com on disk at once: one that actually
# signed the orderer's live TLS cert, and a different, later-generated
# one that the channel/MSP config ended up trusting instead - which shows
# up downstream as "x509: certificate signed by unknown authority" on
# every osnadmin channel join. cryptogen also refuses to write into a
# directory that already has some of its own output, so a half-finished
# CRYPTO_DIR is cleared before regenerating rather than left to produce a
# confusing "already exists" failure.
#
# The marker used to be written unconditionally right after `cryptogen
# generate`, with no check that generation actually finished. cryptogen
# builds its full directory skeleton fast and then writes key/cert
# material into it - if the process is killed partway (a stopped
# container, a Docker Desktop restart mid-run), you can end up with
# every expected directory present but empty, and the marker still goes
# down as "done" anyway. Every later container start then trusts that
# marker, skips regeneration, and the orderer/peers fail forever on a
# missing signcerts directory - a silent, self-perpetuating bad state
# that a reinstall of Docker itself doesn't clear, since it's a bind
# mount living on the host. The marker is now only written after
# explicitly confirming every identity cryptogen was asked to produce
# actually has a signing certificate on disk.
REQUIRED_SIGNCERTS=(
    "$CRYPTO_DIR/ordererOrganizations/example.com/orderers/orderer.example.com/msp/signcerts"
    "$CRYPTO_DIR/peerOrganizations/Hospital_A.example.com/peers/peer0.Hospital_A.example.com/msp/signcerts"
    "$CRYPTO_DIR/peerOrganizations/Hospital_B.example.com/peers/peer0.Hospital_B.example.com/msp/signcerts"
)

crypto_material_complete() {
    local dir
    for dir in "${REQUIRED_SIGNCERTS[@]}"; do
        compgen -G "${dir}/*.pem" > /dev/null 2>&1 || return 1
    done
    return 0
}

if [ -f "$MARKER" ] && crypto_material_complete; then
    echo "Crypto materials already generated and verified (marker found at $MARKER) - skipping."
else
    echo "Crypto materials incomplete or missing - regenerating from scratch..."
    rm -f "$MARKER"
    # $CRYPTO_DIR is itself a bind-mount point (see docker-compose.yml),
    # so `rm -rf "$CRYPTO_DIR"` fails with "Device or resource busy" - you
    # can clear what is mounted there, but not remove the mount point
    # itself from inside the container. Clear its contents instead.
    find "$CRYPTO_DIR" -mindepth 1 -delete
    # cryptogen defaults its --output to "./crypto-config" relative to CWD
    # when the flag is omitted - that is a DIFFERENT directory than
    # $CRYPTO_DIR (".../peer/crypto", no "-config" suffix), so without an
    # explicit --output every past run has been writing into the
    # container's throwaway layer instead of this bind mount, leaving
    # $CRYPTO_DIR permanently empty no matter how many times this runs.
    cryptogen generate --config=/etc/hyperledger/fabric/crypto-config.yaml --output="$CRYPTO_DIR"

    if ! crypto_material_complete; then
        echo "FATAL: cryptogen finished but one or more signing certificates are still missing:" >&2
        for dir in "${REQUIRED_SIGNCERTS[@]}"; do
            compgen -G "${dir}/*.pem" > /dev/null 2>&1 || echo "  missing: $dir" >&2
        done
        exit 1
    fi

    touch "$MARKER"
    echo "Crypto materials generated and verified."
fi

# Start bash
exec /bin/bash
