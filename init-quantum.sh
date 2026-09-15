#!/bin/bash

# Exit on any error
set -e

PEER_CRYPTO_PATH="/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto-config"
QUANTUM_KEY_PATH="/opt/gopath/src/github.com/hyperledger/fabric/peer/quantum_keys"
CRYPTO_CONFIG="/etc/hyperledger/fabric/crypto-config.yaml"

# Function to generate traditional crypto material
generate_traditional_crypto() {
    echo "Generating traditional crypto material..."
    if [ -f "$CRYPTO_CONFIG" ]; then
        cryptogen generate --config=$CRYPTO_CONFIG
    else
        echo "Error: crypto-config.yaml not found at $CRYPTO_CONFIG"
        exit 1
    fi
}

# Function to generate quantum keys
generate_quantum_keys() {
    echo "Generating quantum keys for Fabric 3.0..."
    mkdir -p "${QUANTUM_KEY_PATH}"
    cd /opt/gopath/src/github.com/hyperledger/fabric/peer/

    if [ -f "quantum_cryptogen.py" ]; then
        # Only generate keys if they don't exist
        if [ ! -d "${PEER_CRYPTO_PATH}" ] || [ ! -d "${QUANTUM_KEY_PATH}" ]; then
            python3 quantum_cryptogen.py generate --config="${CRYPTO_CONFIG}"
        else
            echo "Cryptographic materials already exist. Skipping generation."
        fi
    else
        echo "Error: quantum_cryptogen.py not found"
        exit 1
    fi
}

# Only run key generation if the first argument is "generate"
if [ "$1" = "generate" ]; then
    generate_traditional_crypto
    generate_quantum_keys
fi

# Execute original command
exec "$@"