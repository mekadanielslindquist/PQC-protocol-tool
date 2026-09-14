"""Management API for the PQC Protocol Tool.

This package exposes a small FastAPI service that wraps the existing
quantum-key-generation scripts and the various network services defined in
docker-compose.yml, so they can be driven from HTTP calls (and the
management_ui/ test dashboard) instead of ad-hoc shell commands.

Nothing in here changes the behaviour of the underlying services in
sip_connect/, quantum_mqtt/, quantum_srtp/, etc. It only wraps them.
"""
