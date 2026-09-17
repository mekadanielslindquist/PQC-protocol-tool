#!/bin/bash

# Compile Falcon-1024 library for ARM64 Linux
#
# Builds from the "clean" (portable reference) source directory, not
# "aarch64" - the aarch64 variant contains inline asm that doesn't
# reliably compile and isn't needed for the shared library (see
# Dockerfile.cli, which already builds it this way and was the working
# reference this script was brought in line with). The "clean" sources
# also produce PQCLEAN_FALCON1024_CLEAN_* symbols, which is what
# falcon_wrapper.py actually looks up - the previous version of this
# script built the "aarch64" directory's own sources directly, which
# exports PQCLEAN_FALCON1024_AARCH64_* symbols instead and failed at
# import time with "undefined symbol:
# PQCLEAN_FALCON1024_CLEAN_crypto_sign_keypair" even once the library
# itself loaded. Output still lands at .../aarch64/libfalcon-1024_aarch64.so,
# matching the path falcon_wrapper.py loads.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR/PQClean/crypto_sign/falcon-1024/clean"

cc -shared -fPIC -O2 -std=c99 -I../../../common \
    codec.c common.c fft.c fpr.c keygen.c pqclean.c rng.c sign.c vrfy.c \
    ../../../common/fips202.c ../../../common/randombytes.c \
    -o ../aarch64/libfalcon-1024_aarch64.so

chmod 755 ../aarch64/libfalcon-1024_aarch64.so

echo "Library compilation complete:"
command -v file >/dev/null 2>&1 && file ../aarch64/libfalcon-1024_aarch64.so || ls -la ../aarch64/libfalcon-1024_aarch64.so
