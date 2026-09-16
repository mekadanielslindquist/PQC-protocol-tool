# Quantum-Secure Healthcare Blockchain Network


https://github.com/user-attachments/assets/113ddce1-e744-4be9-b4b7-4c4ee51ac121


This project implements a quantum-resistant healthcare information exchange platform using Hyperledger Fabric and Hedera Hashgraph, with post-quantum cryptography (PQC) integrated throughout all communication layers.

## Current Status (as of September 2026)

This is an actively developed prototype, not a production system. Current
state of the Docker stack:

- **Builds and comes up.** All services build clean and reach a running
  state: both Fabric peers, the orderer, `cli`, `couchdb`, `wallet-service`,
  `asterisk`, `quantum_sip`/`quantum_srtp`/`quantum_mqtt`, and
  `libp2p-bridge` all start successfully with `docker compose up -d`.
- **Channel creation is not yet confirmed working end-to-end.**
  `configtxgen` was hitting a parsing error specific to `Admins` signature
  policies (`OR('<MSPID>.admin')`) - see Troubleshooting #5 below. A fix
  (pinning `fabric-tools` to `2.5.14` instead of `:latest`) is in place but
  not yet verified against a clean channel-creation run.
- **`libp2p-bridge` runs and is healthy, but peer discovery finds 0 peers.**
  There is currently only one `libp2p-bridge` service defined (for
  `Hospital_A`) - there is no `Hospital_B` counterpart for it to discover,
  so this isn't a config problem so much as a missing second service. See
  Troubleshooting #4.
- **`hedera-bridge` runs but has two known protocol bugs** that will stop
  it from actually functioning even though the container itself stays up:
  it's configured to reach `wallet-service` over `https://`, but
  `wallet-service` only serves plain HTTP; and `FABRIC_GATEWAY_URL` points
  at a Fabric peer's raw gRPC port, while `hedera_bridge.py` makes plain
  REST calls (`requests.post(...)`) against it - gRPC doesn't speak REST,
  so those calls fail regardless of the URL scheme.
- **Only one organization's infrastructure is real right now.** Hospital A
  runs everything on one machine; Hospital B isn't yet a truly independent
  node. The plan is to stand up Hospital B on its own host (an Oracle Cloud
  Always Free ARM instance, matching this project's existing
  `linux/arm64` platform target) rather than simulating both organizations
  as containers sharing one Docker daemon.

None of the above blocks development - the stack is stable enough to work
against - but "two hospitals actually exchanging quantum-secured,
ledger-verified communications" hasn't been demonstrated end-to-end yet.

## Architecture Overview

The system connects healthcare organizations (Hospital A and Hospital B) via multiple secure communication channels:

- **Hyperledger Fabric** - Enterprise blockchain for storing verifiable healthcare records
- **Hedera Hashgraph** - For additional consensus and immutable timestamping
- **Asterisk PBX** - For secure voice/video communication with quantum-enhanced SRTP
- **MQTT** - For secure messaging with quantum-resistant encryption
- **Libp2p** - For peer-to-peer networking between organizations

### Post-Quantum Cryptography

The system implements two quantum-resistant algorithms:

- **Falcon-1024** - For digital signatures, replacing ECDSA
- **Kyber-512** - For key encapsulation, replacing RSA/Diffie-Hellman

## Components

### Core Services

| Service | Description | Port(s) |
|---------|-------------|---------|
| `peer0.Hospital_A.example.com` | Fabric peer for Hospital A | 7051 |
| `peer0.Hospital_B.example.com` | Fabric peer for Hospital B | 7061 |
| `orderer.example.com` | Hyperledger Fabric orderer | 7050 |
| `couchdb` | State database for Fabric | 5984 |
| `asterisk` | Quantum-enhanced SIP/VoIP server | 5060-5062, 8088, 8089 |
| `quantum_sip` | SIP service with quantum security | 8000 |
| `quantum_srtp` | Secure Real-time Transport with quantum enhancements | - |
| `quantum_mqtt` | MQTT client with quantum security | - |
| `mqtt` | MQTT broker | 1883, 9883 |
| `wallet-service` | Hedera wallet operations | 3000 |
| `hedera-bridge` | Bridge between Hyperledger and Hedera | - |
| `libp2p-bridge` | P2P networking between organizations | 4001, 8085 |
| `minio` | Object storage | 9000, 9001 |
| `timescaledb` | Time-series database | 5432 |

### Security Components

- **HybridSecuritySystem** - Combines quantum and classical cryptography
- **PostQuantumSessionSecurity** - Session management with quantum resistance
- **QuantumEnhancedSRTP** - Secure Real-time Transport Protocol with quantum key exchange
- **EnhancedEncryption** - Encryption layer with quantum entropy
- **SecureKeyManager** - Manages Falcon and Kyber keys

## Setup and Configuration

### Prerequisites

- Docker and Docker Compose
- Python 3.9+
- Hyperledger Fabric binaries (cryptogen, configtxgen)
- Network access for Hedera integration

### Installation

1. Clone the repository:
   

2. Build the Docker images:
   ```bash
   docker-compose build
   ```

3. Generate cryptographic materials:
   ```bash
   ./init-quantum.sh generate
   ```

4. Start the network:
   ```bash
   docker-compose up -d
   ```

### Python dependencies

`requirements.txt` at the repo root installs everything and is what the
Dockerfiles use. It's split by layer under `requirements/` so you can
install less when you don't need all of it:

| File | Covers | Notes |
|---|---|---|
| `requirements/base.txt` | Shared basics (requests, pyyaml, paho-mqtt, ...) | Pulled in by every file below |
| `requirements/api.txt` | The management API + dashboard, `sip_connect/app.py` | No compiled deps - installs quickly everywhere |
| `requirements/crypto.txt` | Falcon/Kyber key generation, qiskit entropy generator | The heavy one (numpy/scipy/qiskit-aer); most likely place a `pip install` fails on an unusual Python version |
| `requirements/infra.txt` | libp2p bridge, data/RPC glue | |

If you just want the management API and test dashboard running (service
status, docker checks, key inspection) without fighting a numpy/qiskit
build:

```bash
pip install -r requirements/api.txt
```

For everything, including actual Falcon/Kyber key generation:

```bash
pip install -r requirements.txt
```

## Management API and Test Dashboard

`management_api/` is a small FastAPI service that wraps the scripts and
containers above so they can be driven over HTTP instead of ad-hoc shell
commands, and `management_ui/` is a single-page dashboard that talks to it -
useful for exercising the stack while you're developing against it.

Run it (see "Python dependencies" above for what to install first -
`requirements/api.txt` is enough to get the dashboard itself running):

```bash
python -m uvicorn management_api.main:app --reload --port 8080
```

Then open `http://localhost:8080/` for the dashboard, or hit the API
directly - interactive docs are at `http://localhost:8080/docs`.

What it exposes (all under `/api`):

| Endpoint | What it does |
|---|---|
| `GET /health` | Liveness check for the API itself |
| `GET /crypto/status` | Whether the compiled Falcon/Kyber native libraries loaded |
| `GET /docker/status` | Whether `docker compose ps` is reachable from here |
| `GET /services` | Status of every service in `docker-compose.yml` (via `docker compose ps` and a TCP port probe, whichever is available) |
| `GET /services/topology` | Parsed service/port info from `docker-compose.yml` |
| `GET /keys/organizations` | The organizations this deployment knows about |
| `GET /keys/{org_id}` | Which key files exist for an org, with public-key fingerprints (never returns private key bytes) |
| `POST /keys/generate` | Generate a fresh Falcon-1024 + Kyber-512 keypair for `Hospital_A` or `Hospital_B` |
| `POST /test/mqtt` | Publish a test message to the MQTT broker |
| `POST /test/blockchain/query` | Run a chaincode query through the `cli` container |
| `POST /test/hedera/health` | Check the Hedera↔Fabric bridge's own health report |

Everything here is best-effort by design: most of this stack (Fabric
peers, the MQTT broker, the compiled PQC libraries) usually isn't running
yet on a fresh checkout, so every endpoint is written to report a clear
reason ("MQTT broker unreachable at mqtt:1883", "Falcon library not
compiled for this platform", etc.) instead of crashing. `GET /crypto/status`
in particular will report `available: false` on most machines until
`sip_connect/compile_falcon_library.sh` (which targets aarch64) and the
equivalent Kyber build have been run for your CPU architecture - that's
expected, not a bug in the API.

## Repository Hygiene

This repo previously had a full Python virtualenv (`sip_connect/.venv`,
~16k files), generated runtime data (`timescaledb-data/`, `couchdb/`,
`mosquitto/` data, `logs/`), and real key/certificate material committed to
git, despite `.gitignore` already listing most of those paths. All of that
has been removed from tracking (left on disk locally, just no longer
version-controlled) - see the "Strip vendored venv, generated data, and
committed secrets from tracking" commit. If you're setting this up fresh:

- Run `pip install -r requirements.txt` to get your own virtualenv/deps.
- `sip_connect/kyber` and `sip_connect/PQClean` are now real git
  submodules - run `git submodule update --init --recursive` after
  cloning.
- Keys and crypto-config are generated locally by `init-quantum.sh` /
  `quantum_cryptogen.py`, not shipped in the repo.

## Configuration Files

- `crypto-config.yaml` - Organization and cryptographic setup
- `docker-compose.yml` - Container configuration
- `network_config.yaml` - Fabric network configuration
- `configs/asterisk/*.conf` - Asterisk configuration files

## Troubleshooting Known Issues

### 1. Orderer Configuration

The orderer requires proper quantum key configuration:

1. Ensure orderer keys are generated:
   ```bash
   # Check if orderer keys exist
   ls -la crypto-config/ordererOrganizations/example.com/orderers/orderer.example.com/quantum_keys/
   
   # If missing, run
   python quantum_cryptogen.py generate --config=crypto-config.yaml
   ```

2. Update orderer environment in `docker-compose.yml`:
   ```yaml
   orderer.example.com:
     environment:
       # Add these specific configurations
       - ORDERER_GENERAL_QUANTUM_ENABLED=true
       - ORDERER_GENERAL_QUANTUM_KEYSTORE=/var/hyperledger/orderer/quantum_keys
       - ORDERER_GENERAL_QUANTUM_KEYTYPES=["Falcon","Kyber"]
   ```

### 2. Hospital B Handshake Issues

Possible fixes for Hospital B handshake issues:

1. Check TLS certificates:
   ```bash
   # Verify certificates exist
   ls -la crypto-config/peerOrganizations/Hospital_B.example.com/peers/peer0.Hospital_B.example.com/tls/
   ```

2. Ensure quantum keys are properly generated:
   ```bash
   # Check quantum keys
   ls -la keys/Hospital_B.example.com/
   ```

3. Check network connectivity:
   ```bash
   # From Hospital A container
   docker exec -it peer0.Hospital_A.example.com ping peer0.Hospital_B.example.com
   
   # Test TLS connection
   docker exec -it peer0.Hospital_A.example.com openssl s_client -connect peer0.Hospital_B.example.com:7061
   ```

4. Update SIP configuration in `configs/asterisk/pjsip.conf`:
   ```
   [Hospital_B_endpoint]
   type=endpoint
   transport=transport-tls
   context=from-external
   disallow=all
   allow=ulaw
   allow=alaw
   aors=Hospital_B_endpoint
   auth=Hospital_B_auth
   direct_media=no
   trust_id_inbound=yes
   ```

### 3. Asterisk Not Starting Automatically

To fix Asterisk auto-start issues:

1. Update entrypoint script permissions:
   ```bash
   chmod +x entrypoint_asterisk.sh
   ```

2. Check Asterisk module:
   ```bash
   # Verify module exists
   ls -la asterisk_modules/res_quantum/res_quantum.so
   
   # Ensure module is loaded in config
   grep "res_quantum" configs/asterisk/modules.conf
   ```

3. Update `modules.conf`:
   ```
   [modules]
   autoload=yes
   load => res_quantum.so
   ```

4. Fix directory permissions in Docker startup:
   ```
   # Add to entrypoint_asterisk.sh
   chmod -R 750 /etc/asterisk
   chown -R asterisk:asterisk /etc/asterisk
   ```

### 4. Libp2p Connection Issues (`Discovered 0 peers`)

As of this writing, `libp2p-bridge` runs and passes its own healthcheck,
but its peer-discovery loop always logs `Discovered 0 peers`. Diagnosed
root causes, in order of how much they matter:

1. **There's no Hospital_B bridge to find.** `docker-compose.yml` only
   defines one `libp2p-bridge` service (`ORG_ID=Hospital_A`). Discovery
   can never succeed until a second bridge service exists for Hospital_B,
   on its own host or its own container, with `ORG_ID=Hospital_B` and its
   own key/cert mounts.

2. **`PEER_ADDRESSES` is in the wrong format for the code that reads it.**
   `libp2p/libp2p_bridge.py`'s `discover_peers()` expects entries shaped
   like `OrgName:port` (e.g. `Hospital_B:8085`) and builds candidate
   hostnames itself (`peer0.{org}.example.com`, etc.). The compose file
   currently sets `PEER_ADDRESSES=peer0.Hospital_B.example.com:7061` - an
   already-fully-qualified hostname, which the code then wraps *again*,
   producing a garbled `peer0.peer0.Hospital_B.example.com.example.com`
   (visible in the container logs). It also points at port `7061`, the
   Fabric peer's gRPC port - discovery is bridge-to-bridge, so once a
   Hospital_B bridge exists, this needs to point at *its* HTTP port
   (`8085`), not the peer's.

3. **The `[Errno 21] Is a directory` TLS error is expected, not a bug.**
   There's no real TLS cert material anywhere in this repo for this
   bridge yet - `certificates/` is empty stub directories. `start()` in
   `libp2p_bridge.py` correctly falls back to plain HTTP when it can't
   load a cert chain, and the healthcheck was already updated to match
   (plain `http://localhost:8085/health`, checking the actual status code
   instead of just "didn't raise"). Don't chase this one until real certs
   are generated for the bridge.

Once a real Hospital_B host exists (see "Current Status" above), fix #2
and add its own bridge service per #1 before expecting discovery to work.


### 5. `cli` / `configtxgen` fails on Admin signature policies

`configtxgen` (invoked from the `cli` container during channel creation)
can fail with an error like:

```
invalid signature policy rule 'OR('Hospital_AMSP.admin')': Unable to
access unexported field 'admin' in token 'Hospital_AMSP.admin'
```

This is *not* a misconfiguration in `network_config.yaml`/`configtx.yaml`
- the policy syntax (`OR('<MSPID>.admin')`) is standard Fabric, byte-level
identical in structure to the working `.member` policies right next to it
in the same file, and NodeOUs is correctly enabled in the org's MSP
`config.yaml`. The error wording itself ("unable to access unexported
field ... in token") is the literal error format used by `govaluate` (a Go
expression-parsing library) - not Fabric's normal policy-parser error
text - which points at a real, narrow bug in how this Fabric build's
policy parser handles the `admin` role token specifically (every `.member`
policy in the same config works fine).

`hyperledger/fabric-tools` was never released past `2.5.16` on Docker Hub
- there is no `3.0.0` tag for it, even though `fabric-peer` and
`fabric-orderer` do have one and this project uses `3.0.0` for both. So
`Dockerfile.cli`'s `FROM hyperledger/fabric-tools:latest` (`2.5.16`) is
running a full major version behind the rest of the stack, with no exact
match available. `Dockerfile.cli` is currently pinned to
`hyperledger/fabric-tools:2.5.14` as a test of whether this is a
regression specific to a recent 2.5.x patch - **not yet confirmed working**
against a real channel-creation run. If `2.5.14` hits the same error, the
bug predates that patch and this needs a different workaround (possibly
restructuring how the `Admins` policy is expressed, or building
`configtxgen` from source).

## Integration Testing

After resolving configuration issues, test the full integration:

1. Initialize the blockchain with test data:
   ```bash
   docker exec -it cli ./entrypoint.sh
   ```

2. Test SIP connectivity:
   ```bash
   # From Hospital A to Hospital B
   docker exec -it asterisk asterisk -rx "pjsip show endpoint Hospital_B_endpoint"
   ```

3. Test Hedera integration:
   ```bash
   # Submit test transaction
   docker exec -it hedera-bridge python3 -c "from hedera_bridge import HederaFabricBridge; bridge = HederaFabricBridge('Hospital_A'); print(bridge.check_health())"
   ```

## License


## Contact

For assistance, please contact meka.lindquist@mirrorwell.io
