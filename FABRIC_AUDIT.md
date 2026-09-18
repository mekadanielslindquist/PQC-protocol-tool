# Hyperledger Fabric Code Audit

A pass over every Fabric-specific piece of this repo (`crypto-config.yaml`, `network_config.yaml`,
`docker-compose.yml`'s Fabric services, `entrypoint.sh`, `Dockerfile.peer`, `Dockerfile.cli`,
`management_api/routers/fabric.py`, and everything under `chaincode/`), checked against Hyperledger Fabric's
current official documentation (Fabric 3.x line, current release v3.1.5 as of this audit, v2.5.16 as the
parallel LTS line). This is an honest snapshot, not a certification - some of it is "correct and current,"
some of it is "correct but a deliberate prototype simplification," and a couple of items are genuine gaps
worth fixing before this touches anything that isn't test data.

## Correctly aligned with current Fabric best practice

- **Channel Participation API, not the legacy system channel.** The orderer runs with
  `ORDERER_GENERAL_BOOTSTRAPMETHOD=none` and `ORDERER_CHANNELPARTICIPATION_ENABLED=true`, and
  `management_api/routers/fabric.py` creates channels with `osnadmin channel join` against a per-channel
  genesis block - not `peer channel create` against a system channel. This is the documented, current way to
  do it; the older bootstrap-genesis-block-at-orderer-startup model this project's channel API deliberately
  moved away from is legacy.
- **etcdraft ordering**, not Kafka (fully removed in Fabric 3.x) or Solo (test-only, being phased out). Correct
  choice for the only supported production consensus mechanism, alongside the newer SmartBFT option this
  project doesn't need.
- **NodeOUs enabled** (`EnableNodeOUs: true` in `crypto-config.yaml`, `NodeOUs` blocks defined per org) rather
  than relying on the deprecated `admincerts`-based admin designation.
- **mTLS on the orderer admin endpoint** (`ORDERER_ADMIN_TLS_ENABLED`, `_CLIENTROOTCAS`, `_CLIENTCERTIFICATE`,
  `_CLIENTKEY` all set, plus `ORDERER_GENERAL_TLS_CLIENTAUTHREQUIRED=true`) - Fabric's orderer panics at startup
  if the admin endpoint has TLS enabled without mTLS, and this repo has it configured correctly.
- **Chaincode lifecycle flow in `fabric.py`** (`package` → `install` on both orgs' peers → `calculatepackageid`
  → `approveformyorg` for both orgs → `commit`) matches the documented Fabric 2.x/3.x lifecycle exactly, including
  real edge-case handling that's easy to get wrong: `osnadmin` exits 0 even on an HTTP 4xx/5xx from the admin
  API, so `_osnadmin_ok()` explicitly parses the `Status:` line instead of trusting the return code; re-running
  channel/chaincode creation is idempotent via "already exists"/"already successfully installed" string checks
  instead of failing on second use.
- **`network_config.yaml`'s two anchor-peer ports (Hospital_A on 7051, Hospital_B on 7061) are correct, not a
  bug.** This looked like a copy-paste error on first read, but cross-checking against `docker-compose.yml`
  confirms each peer's own `CORE_PEER_LISTENADDRESS`/`CORE_PEER_GOSSIP_EXTERNALENDPOINT`/port mapping
  genuinely differs per org (7051 vs 7061) - the anchor peer config is just reporting each peer's real address.
  Flagging this as closed, not open.

## Expected simplifications for a prototype (not bugs, but not production-ready either)

- **`cryptogen` instead of Fabric CA.** Fabric's own docs are explicit that `cryptogen` is a test/demo tool;
  a real deployment issues identities through Fabric CA's register/enroll flow instead. This repo's
  `entrypoint.sh` actually handles `cryptogen`'s failure modes unusually carefully (a completion marker that's
  only written after verifying every expected signcert actually landed on disk, specifically to avoid the
  partial-generation bug that caused this project's earlier "x509: unknown authority" incidents) - so the
  *use* of `cryptogen` is a known, reasonable prototype choice, not a sign of carelessness. Worth switching to
  Fabric CA before any real identities are at stake.
- **Single-node etcdraft cluster** (`orderer.example.com` is the only consenter in both channel profiles).
  This is the correct mechanism, but a one-node Raft cluster has no fault-tolerance benefit over Solo - it's a
  dev/demo topology. A real deployment wants an odd-numbered cluster (3 or 5 nodes).
- **Peer-to-peer/client TLS is server-side only**, not mutual (`CORE_PEER_TLS_ENABLED=true` on peers, but no
  `CORE_PEER_TLS_CLIENTAUTHREQUIRED`). This matches Fabric's documented behavior - general peer/client mTLS is
  opt-in, not automatic, unlike the orderer admin endpoint - so it's not wrong, just less hardened than it could
  be. Worth enabling for a genuinely zero-trust multi-org setup.

## Genuine gaps worth fixing

1. **No private data collections anywhere in `chaincode/`.** All three chaincode modules
   (`quantum_records/peer_to_peer.go`, `quantum-communications/quantum_comm.go`,
   `quantum-hedera-integration/quantum_hedera.go`) write exclusively through `PutState`/`GetState` - i.e. the
   shared world state, visible identically to both Hospital_A and Hospital_B. There's no
   `collections_config.json` in the repo and `fabric.py`'s chaincode/deploy endpoint never passes
   `--collections-config` to `peer lifecycle chaincode commit`. As it stands today none of the defined structs
   (`Communication`, `Device`, `MasterWallet`, `SubWallet`, `Payment`) are literal patient/PHI records, so this
   isn't a live compliance violation - but given the project's two-hospital healthcare framing, this is the
   mechanism Fabric provides specifically for "one org's data shouldn't be readable by the other org," and
   nothing in the current chaincode uses it. Worth deciding deliberately before any patient-identifiable field
   gets added to one of these structs, rather than discovering the gap after the fact.
2. **Chaincode Go modules aren't vendored and have no `go.sum`.** All three `chaincode/*/go.mod` files declare a
   single dependency (`github.com/hyperledger/fabric-contract-api-go v1.2.2`) with no `vendor/` directory and no
   `go.sum` anywhere under `chaincode/`. `peer lifecycle chaincode package` only tars up the source; the actual
   `go build` happens later, inside the chaincode's own container, when it's installed - and that build will try
   to fetch the module from the Go module proxy over the network at that point. Given this same project has
   already hit real network-egress restrictions in at least one environment this session (pip installs, GitHub
   HTTPS both blocked), an unvendored dependency is a concrete way for `peer lifecycle chaincode install` to
   fail in exactly the kind of restricted environment this project has already been built in. Fix is either
   `go mod vendor` (checked into each chaincode module) or moving to Fabric's chaincode-as-a-service (ccaas)
   pattern with a pre-built image - both are standard, documented options; right now this repo does neither.

   **Researched, not yet applied:** confirmed against Fabric's own docs - `peer lifecycle chaincode package`
   only tars the source, the real `go build` happens later inside the peer's ephemeral chaincode build
   container via the external-builder framework, and Fabric's "Writing Your First Chaincode" doc states
   plainly that a chaincode's non-stdlib dependencies "must be included in your chaincode package when it is
   installed to a peer" - i.e. don't assume that build container can reach a module proxy
   (https://hyperledger-fabric.readthedocs.io/en/latest/chaincode4ade.html). `go mod vendor` is the documented
   fix, not a workaround: run `go mod tidy && go mod vendor` inside each of the three `chaincode/*/` module
   directories, then commit the resulting `vendor/` folder alongside `go.mod`/`go.sum`. Compared to migrating to
   chaincode-as-a-service (ccaas) - which relocates the build step off the peer but doesn't remove the network
   requirement, and adds a long-lived service + connection/TLS plumbing per chaincode - vendoring is the right
   fit here: three small modules, one dependency each, zero changes needed to `management_api/routers/fabric.py`
   or the docker-compose deploy flow. Practically, this needs to run inside the `cli` container (it already has
   Go 1.21 installed per `Dockerfile.cli`, and `chaincode/` is bind-mounted into it at
   `/opt/gopath/src/github.com/hyperledger/fabric/peer/chaincode` per `docker-compose.yml`), so it has to wait
   for the stack to be up - not run from a bare shell that lacks both `go` and network access to
   `proxy.golang.org`.
3. **Fabric version is pinned to exactly `3.0.0` for peers/orderer, but the `cli` image floats on `:latest`.**
   `docker-compose.yml` and `Dockerfile.peer` pin `hyperledger/fabric-peer:3.0.0` /
   `hyperledger/fabric-orderer:3.0.0` explicitly, but `Dockerfile.cli` builds from
   `hyperledger/fabric-tools:latest` - an unpinned tag. Two problems: `3.0.0` itself is now superseded within
   the 3.x line (current is `v3.1.5`), and more concretely, `:latest` on the tools image means the `peer`/
   `configtxgen`/`osnadmin` binaries `fabric.py` shells out to can silently drift to a newer minor/patch version
   than the peer/orderer binaries actually running the network, on a plain image rebuild with no code change.
   Pin all four Fabric images to the same explicit version.

## Not fully evaluated (out of scope for this pass)

- The actual generated MSP folder contents under `crypto-config/` (whether NodeOUs config is being honored
  correctly at generation time, not just declared in `crypto-config.yaml`) - would need a live `cryptogen`
  run and inspection to confirm, not just the config source.
- The `quantum_records`/`quantum-communications`/`quantum-hedera-integration` chaincode's actual business
  logic correctness (endorsement policy design, access control inside the chaincode itself beyond what
  Fabric's channel/MSP layer already enforces) - this pass checked Fabric *platform* usage, not chaincode
  application logic.
- `TwoOrgsOrdererGenesis`'s `Consortiums.SampleConsortium` block in `network_config.yaml` appears to be dead
  configuration - `fabric.py` only ever invokes `configtxgen -profile TwoOrgsChannel`, and that profile's own
  code comment explains it deliberately doesn't reference a Consortium because this orderer has no system
  channel. Low priority (it's inert, not broken), but worth deleting or clearly marking as legacy so a future
  reader doesn't assume it's the active bootstrap path.
