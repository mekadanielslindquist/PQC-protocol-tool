#!/bin/bash
set -e

# Runtime directories should already exist and be owned by asterisk:asterisk
# from the image build (see Dockerfile.asterisk), but re-assert this at
# container start too in case a bind-mounted volume overrode ownership.
mkdir -p /var/run/asterisk /var/log/asterisk /var/spool/asterisk
chown -R asterisk:asterisk /var/run/asterisk /var/log/asterisk /var/spool/asterisk /etc/asterisk 2>/dev/null || true

# Run Asterisk in the foreground (-f) as PID 1 so Docker can manage its
# lifecycle directly - signals, restart policy, and `docker logs` all need
# the actual Asterisk process to be PID 1, not a background daemon forked
# off by a wrapper script that then exits (which is what an empty/no-op
# entrypoint effectively did: nothing ran, so the container had nothing to
# stay alive for).
exec /usr/sbin/asterisk -f -U asterisk -G asterisk -vvv
