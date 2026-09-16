import asyncio
import json
import logging
import os
import time
import uuid
import ssl
import socket
import hashlib
from typing import Dict, List, Any
import aiohttp
from aiohttp import web
import base64
from pathlib import Path

# Add paths for importing quantum security modules
import sys

base_dir = Path(__file__).parent.parent
sys.path.append(str(base_dir))
sys.path.append(str(base_dir / 'sip_connect'))

# Import quantum security modules

from sip_connect.key_utils import load_and_convert_keys
from sip_connect.falcon_wrapper import falcon_sign_message, falcon_verify_signature
from sip_connect.kyber_wrapper import kyber_encap, kyber_decap

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Debug for detailed logging
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('libp2p_bridge.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('LibP2PBridge')


class QuantumLibP2PBridge:
    """
    Bridge implementation that provides P2P communication with quantum security
    """

    def __init__(self, org_id: str, peer_addresses: List[str], fabric_url: str):
        self.org_id = org_id
        self.peer_addresses = peer_addresses
        self.fabric_url = fabric_url
        self.node_id = str(uuid.uuid4())
        self.peers = {}  # Connected peers
        self.messages = []  # Message queue
        self.app = None
        self.runner = None
        self.site = None
        self.discovery_interval = 60  # Run discovery every 60 seconds

        # Load quantum keys
        self._load_quantum_keys()
        logger.info(f"Initialized QuantumLibP2PBridge for {org_id} with quantum keys")

    def _load_quantum_keys(self):
        """Load quantum keys for the organization"""
        try:
            # Try to load keys using the shared key_utils module
            converted_keys = load_and_convert_keys(self.org_id)

            # Store the converted keys
            self.falcon_public_key = converted_keys['falcon_public']
            self.falcon_private_key = converted_keys['falcon_private']
            self.kyber_public_key = converted_keys['kyber_public']
            self.kyber_private_key = converted_keys['kyber_private']

            logger.info(f"Successfully loaded quantum keys for {self.org_id}")

            # Verify keys by testing signing and encryption
            self._verify_keys()

        except Exception as e:
            logger.error(f"Failed to load quantum keys: {e}")
            raise

    def _verify_keys(self):
        """Verify that loaded keys work properly"""
        try:
            # Test message
            test_message = f"Verification test message for {self.org_id}".encode()

            # Test Falcon signature
            signature = falcon_sign_message(test_message, self.falcon_private_key)
            verification_result = falcon_verify_signature(test_message, signature, self.falcon_public_key)

            if not verification_result:
                raise ValueError("Falcon key verification failed")

            # Test Kyber key encapsulation
            shared_secret, ciphertext = kyber_encap(self.kyber_public_key)
            decapped_secret = kyber_decap(ciphertext, self.kyber_private_key)

            if shared_secret != decapped_secret:
                raise ValueError("Kyber key verification failed")

            logger.info("Quantum keys verified successfully")

        except Exception as e:
            logger.error(f"Key verification failed: {e}")
            raise

    async def start(self, host: str = '0.0.0.0', port: int = 8085):
        """Start the P2P bridge service with TLS/HTTPS"""
        # Create an SSL context for server
        try:
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)

            # Prefer the paths docker-compose.yml actually configures
            # (TLS_CERT_PATH/TLS_KEY_PATH/TLS_CA_PATH) over guessing from
            # org_id - these env vars were being set in docker-compose.yml
            # but never read here, so the cert path used at runtime could
            # silently diverge from what was actually intended/mounted.
            cert_path = os.environ.get(
                "TLS_CERT_PATH",
                f"/app/certificates/{self.org_id}_Secure_Communications_certificate.pem",
            )
            key_path = os.environ.get(
                "TLS_KEY_PATH",
                f"/app/certificates/{self.org_id}_Secure_Communications_private_key.pem",
            )
            ca_path = os.environ.get(
                "TLS_CA_PATH", f"/app/certificates/{self.org_id}_ca.pem"
            )

            # Check if paths exist, use default paths if not
            if not os.path.exists(cert_path):
                logger.warning(f"Certificate not found at {cert_path}, using default paths")
                cert_path = "/app/certificates/server.crt"
                key_path = "/app/certificates/server.key"
                ca_path = "/app/certificates/ca.pem"

            # Double check default paths
            if not os.path.exists(cert_path):
                logger.warning(f"Default certificate not found at {cert_path}")
                # List certificate directory contents for debugging
                cert_dir = Path("/app/certificates")
                if cert_dir.exists():
                    logger.info(f"Certificate directory contents: {list(cert_dir.glob('*'))}")

                    # Try to find any certificate files
                    cert_files = list(cert_dir.glob("*.pem")) + list(cert_dir.glob("*.crt"))
                    if cert_files:
                        cert_path = str(cert_files[0])
                        logger.info(f"Using first found certificate: {cert_path}")

                        # Try to find corresponding key file
                        key_files = list(cert_dir.glob("*key*.pem")) + list(cert_dir.glob("*.key"))
                        if key_files:
                            key_path = str(key_files[0])
                            logger.info(f"Using first found key: {key_path}")

            if os.path.exists(cert_path) and os.path.exists(key_path):
                ssl_context.load_cert_chain(cert_path, key_path)
                if os.path.exists(ca_path):
                    ssl_context.load_verify_locations(ca_path)

                # Log the TLS configuration
                logger.info(f"TLS configured with certificates: {cert_path}, {key_path}")
                use_ssl = True
            else:
                logger.error("Required certificate files not found")
                logger.warning("Falling back to HTTP (insecure)")
                ssl_context = None
                use_ssl = False
        except Exception as e:
            logger.error(f"Error setting up TLS: {e}")
            logger.warning("Falling back to HTTP (insecure)")
            ssl_context = None
            use_ssl = False

        # Create web app for P2P
        self.app = web.Application()
        self.app.add_routes([
            web.get('/health', self.handle_health),
            web.post('/message', self.handle_message),
            web.get('/peers', self.handle_get_peers),
            web.post('/peers', self.handle_register_peer),
            web.get('/node_id', self.handle_get_node_id),
            # Add endpoint for public quantum key exchange
            web.get('/public_keys', self.handle_get_public_keys),
        ])

        # Start web server
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        if use_ssl:
            self.site = web.TCPSite(self.runner, host, port, ssl_context=ssl_context)
            protocol = "https"
        else:
            self.site = web.TCPSite(self.runner, host, port)
            protocol = "http"

        await self.site.start()

        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        logger.info(f"LibP2P bridge for {self.org_id} started on {protocol}://{host}:{port}")
        logger.info(f"Hostname: {hostname}, IP: {ip_address}")
        logger.info(f"Node ID: {self.node_id}")

        # Allow time for services to start
        await asyncio.sleep(10)

        # Start periodic discovery process
        asyncio.create_task(self.periodic_discover_peers())

        # Start background tasks
        asyncio.create_task(self.process_messages())

        return self.app

    async def stop(self):
        """Stop the P2P bridge service"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("LibP2P bridge stopped")

    async def periodic_discover_peers(self):
        """Periodically discover peers"""
        while True:
            logger.info("Starting peer discovery...")
            await self.discover_peers()
            await asyncio.sleep(self.discovery_interval)

    async def discover_peers(self):
        """Discover and connect to peers"""
        for peer_addr in self.peer_addresses:
            try:
                # Trim any whitespace
                peer_addr = peer_addr.strip()
                if not peer_addr:
                    continue

                # Format: "Hospital_B:4001"
                parts = peer_addr.split(':')
                if len(parts) != 2:
                    logger.error(f"Invalid peer address format: {peer_addr}")
                    continue

                org_id, port = parts


                # Try multiple possible hostnames
                hostnames = [
                    f"peer0.{org_id}.example.com",  # Docker container name (most likely to work)
                    f"{org_id}.example.com",  # FQDN format
                    org_id,  # Service name
                    org_id.lower()  # Lowercase service name
                ]

                connected = False

                for hostname in hostnames:
                    # Try both https and http
                    protocols = ["https", "http"]

                    for protocol in protocols:
                        url = f"{protocol}://{hostname}:{port}/node_id"
                        logger.debug(f"Attempting to connect to peer at {url}")

                        try:
                            timeout = aiohttp.ClientTimeout(total=5)  # 5 second timeout
                            connector = aiohttp.TCPConnector(ssl=False if protocol == "http" else None)
                            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                                async with session.get(url) as resp:
                                    if resp.status == 200:
                                        data = await resp.json()
                                        node_id = data.get('node_id')
                                        if node_id:
                                            self.peers[node_id] = {
                                                'org_id': org_id,
                                                'url': f"{protocol}://{hostname}:{port}",
                                                'protocol': protocol,
                                                'last_seen': time.time()
                                            }
                                            logger.info(
                                                f"Discovered peer: {org_id} ({node_id}) at {protocol}://{hostname}:{port}")

                                            # Register with the peer
                                            await self.register_with_peer(
                                                f"{protocol}://{hostname}:{port}/peers"
                                            )

                                            # Get peer's public quantum keys
                                            await self.get_peer_public_keys(
                                                f"{protocol}://{hostname}:{port}/public_keys",
                                                node_id
                                            )

                                            connected = True
                                            break  # Break the protocol loop if connected
                        except Exception as e:
                            logger.debug(f"Could not connect to {url}: {str(e)}")

                    if connected:
                        break  # Break the hostname loop if connected

                if not connected:
                    logger.warning(f"Failed to connect to peer {peer_addr} on all hostnames")

            except Exception as e:
                logger.error(f"Error discovering peer {peer_addr}: {e}", exc_info=True)

        logger.info(f"Discovered {len(self.peers)} peers")

    async def get_peer_public_keys(self, url: str, node_id: str):
        """Get peer's public quantum keys"""
        try:
            protocol = "https" if url.startswith("https") else "http"
            connector = aiohttp.TCPConnector(ssl=False if protocol == "http" else None)

            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        falcon_public = data.get('falcon_public')
                        kyber_public = data.get('kyber_public')

                        if falcon_public and kyber_public and node_id in self.peers:
                            self.peers[node_id]['falcon_public'] = falcon_public
                            self.peers[node_id]['kyber_public'] = kyber_public
                            logger.info(f"Retrieved quantum public keys from peer {self.peers[node_id]['org_id']}")
                    else:
                        logger.warning(f"Failed to get quantum keys from peer: {url}, status: {resp.status}")
        except Exception as e:
            logger.error(f"Error getting quantum keys from peer {url}: {e}")

    async def register_with_peer(self, peer_url: str):
        """Register with a peer"""
        try:
            hostname = socket.gethostname()
            protocol = "https" if peer_url.startswith("https") else "http"

            data = {
                'node_id': self.node_id,
                'org_id': self.org_id,
                'url': f"{protocol}://{hostname}:8085"  # Use actual hostname
            }

            logger.debug(f"Registering with peer at {peer_url} as {data['url']}")

            connector = aiohttp.TCPConnector(ssl=False if protocol == "http" else None)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(peer_url, json=data) as resp:
                    if resp.status == 200:
                        logger.info(f"Successfully registered with peer: {peer_url}")
                    else:
                        response_text = await resp.text()
                        logger.warning(
                            f"Failed to register with peer: {peer_url}, status: {resp.status}, response: {response_text}")
        except Exception as e:
            logger.error(f"Error registering with peer {peer_url}: {e}")

    async def broadcast_message(self, message: Dict[str, Any]):
        """Broadcast a message to all peers with quantum signature"""
        for node_id, peer in self.peers.items():
            try:
                peer_url = f"{peer['url']}/message"
                protocol = peer.get('protocol', 'https')

                # Add sender information
                message['sender'] = {
                    'node_id': self.node_id,
                    'org_id': self.org_id
                }
                message['timestamp'] = time.time()
                message['message_id'] = str(uuid.uuid4())

                # Convert to JSON and then to bytes for signing
                message_bytes = json.dumps(message).encode()

                # Sign with Falcon
                signature = falcon_sign_message(
                    message_bytes,
                    self.falcon_private_key
                )

                # Add signature to message
                message['signature'] = {
                    'algorithm': 'falcon-1024',
                    'value': base64.b64encode(signature).decode('utf-8')
                }

                # If we have the peer's Kyber public key, encrypt the message
                if 'kyber_public' in peer:
                    try:
                        # Decode the hex-encoded Kyber public key
                        kyber_public_bytes = bytes.fromhex(peer['kyber_public'])

                        # Generate a shared secret and encrypt message
                        shared_secret, ciphertext = kyber_encap(kyber_public_bytes)

                        # Create a symmetric encryption key from the shared secret
                        encryption_key = hashlib.sha256(shared_secret).digest()

                        # Use a simple XOR encryption for the message payload
                        # In a production system, use a proper symmetric cipher like AES
                        encrypted_message = self._xor_encrypt(json.dumps(message).encode(), encryption_key)

                        # Create quantum-secured payload
                        quantum_payload = {
                            'ciphertext': base64.b64encode(ciphertext).decode('utf-8'),
                            'encrypted_data': base64.b64encode(encrypted_message).decode('utf-8'),
                            'org_id': self.org_id,
                            'node_id': self.node_id
                        }

                        # Send encrypted message
                        connector = aiohttp.TCPConnector(ssl=False if protocol == "http" else None)
                        async with aiohttp.ClientSession(connector=connector) as session:
                            async with session.post(peer_url, json=quantum_payload) as resp:
                                if resp.status == 200:
                                    logger.info(f"Quantum-encrypted message sent to {peer['org_id']}")
                                else:
                                    logger.warning(f"Failed to send quantum-encrypted message to {peer['org_id']}")
                    except Exception as e:
                        logger.error(f"Failed to use quantum encryption for {peer['org_id']}: {e}")

                        # Fall back to regular message sending
                        connector = aiohttp.TCPConnector(ssl=False if protocol == "http" else None)
                        async with aiohttp.ClientSession(connector=connector) as session:
                            async with session.post(peer_url, json=message) as resp:
                                if resp.status == 200:
                                    logger.info(f"Regular signed message sent to {peer['org_id']}")
                                else:
                                    logger.warning(f"Failed to send message to {peer['org_id']}")
                else:
                    # If we don't have the peer's Kyber key, just send the signed message
                    connector = aiohttp.TCPConnector(ssl=False if protocol == "http" else None)
                    async with aiohttp.ClientSession(connector=connector) as session:
                        async with session.post(peer_url, json=message) as resp:
                            if resp.status == 200:
                                logger.info(f"Regular signed message sent to {peer['org_id']}")
                            else:
                                logger.warning(f"Failed to send message to {peer['org_id']}")
            except Exception as e:
                logger.error(f"Error sending message to {peer.get('org_id', node_id)}: {e}")

    def _xor_encrypt(self, data: bytes, key: bytes) -> bytes:
        """Simple XOR encryption (for demonstration, use AES in production)"""
        # Extend the key to match data length using key derivation
        extended_key = b''
        while len(extended_key) < len(data):
            extended_key += hashlib.sha256(key + extended_key).digest()

        # Perform XOR operation
        return bytes(a ^ b for a, b in zip(data, extended_key[:len(data)]))

    async def process_messages(self):
        """Process messages in the background"""
        while True:
            await asyncio.sleep(1)  # Check every second

            # Process messages
            while self.messages:
                message = self.messages.pop(0)
                await self.handle_process_message(message)

    async def handle_process_message(self, message: Dict[str, Any]):
        """Process a received message with signature verification"""
        try:
            # Check if this is a quantum-encrypted message
            if 'ciphertext' in message and 'encrypted_data' in message:
                logger.debug("Received quantum-encrypted message")
                try:
                    # Decode the base64 encoded ciphertext and encrypted data
                    ciphertext = base64.b64decode(message['ciphertext'])
                    encrypted_data = base64.b64decode(message['encrypted_data'])

                    # Recover the shared secret using our Kyber private key
                    shared_secret = kyber_decap(ciphertext, self.kyber_private_key)

                    # Derive encryption key from shared secret
                    encryption_key = hashlib.sha256(shared_secret).digest()

                    # Decrypt the message using XOR (for demonstration)
                    decrypted_data = self._xor_encrypt(encrypted_data, encryption_key)

                    # Parse the decrypted JSON
                    message = json.loads(decrypted_data.decode('utf-8'))
                    logger.debug("Successfully decrypted quantum message")
                except Exception as e:
                    logger.error(f"Failed to decrypt quantum message: {e}")
                    return  # Skip further processing if decryption fails

            # Extract sender info and signature
            sender = message.get('sender', {})
            signature_data = message.get('signature', {})

            # Skip signature verification if data is missing
            if not sender or not signature_data:
                logger.warning("Message missing sender or signature information")
                # Continue processing anyway for backward compatibility
            else:
                node_id = sender.get('node_id')
                org_id = sender.get('org_id')

                # Try to verify signature if possible
                try:
                    # Extract the signature bytes
                    signature_bytes = base64.b64decode(signature_data.get('value', ''))

                    # Clone the message without the signature for verification
                    verification_message = message.copy()
                    verification_message.pop('signature', None)

                    # Convert to JSON and then to bytes for verification
                    message_bytes = json.dumps(verification_message).encode()

                    # If we know this peer and have their public key, verify
                    if node_id in self.peers and 'falcon_public' in self.peers[node_id]:
                        # Use peer's Falcon public key for verification
                        peer_falcon_public = bytes.fromhex(self.peers[node_id]['falcon_public'])

                        # Verify with Falcon
                        is_valid = falcon_verify_signature(
                            message_bytes,
                            signature_bytes,
                            peer_falcon_public
                        )

                        if not is_valid:
                            logger.warning(f"Invalid signature on message from {org_id}")
                            # Continue processing for compatibility
                    else:
                        logger.warning(f"Cannot verify signature: unknown peer or missing public key for {org_id}")
                except Exception as e:
                    logger.error(f"Error during signature verification: {e}")
                    # Continue processing anyway

            # Process by message type
            message_type = message.get('type')

            if message_type == 'session_update':
                # Forward to Fabric
                await self.forward_to_fabric(message)

            elif message_type == 'key_rotation':
                # Handle key rotation notification
                logger.info(f"Key rotation notification from {sender.get('org_id')}")

                # Update stored public keys if included
                if 'falcon_public' in message and 'kyber_public' in message and node_id in self.peers:
                    self.peers[node_id]['falcon_public'] = message['falcon_public']
                    self.peers[node_id]['kyber_public'] = message['kyber_public']
                    logger.info(f"Updated quantum public keys for peer {sender.get('org_id')}")

            elif message_type == 'peer_discovery':
                # Handle peer discovery
                node_id = sender.get('node_id')
                org_id = sender.get('org_id')
                url = message.get('url')

                if node_id and org_id and url and node_id != self.node_id:
                    protocol = "https" if url.startswith("https") else "http"
                    self.peers[node_id] = {
                        'org_id': org_id,
                        'url': url,
                        'protocol': protocol,
                        'last_seen': time.time()
                    }
                    logger.info(f"Added peer from discovery: {org_id} ({node_id})")

                    # Get peer's public keys
                    await self.get_peer_public_keys(f"{url}/public_keys", node_id)

            else:
                logger.warning(f"Unknown message type: {message_type}")

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

    async def forward_to_fabric(self, message: Dict[str, Any]):
        """Forward a message to Fabric chaincode with quantum signature"""
        try:
            # Extract session info
            session_id = message.get('sessionId')
            status = message.get('status')
            metadata = message.get('metadata', {})

            if not session_id or not status:
                logger.warning("Invalid session update message")
                return

            # Call UpdateSessionStatus chaincode function
            payload = {
                "function": "UpdateSessionStatus",
                "args": [
                    session_id,
                    status,
                    json.dumps(metadata)
                ]
            }

            # Convert to JSON and then to bytes for signing
            payload_bytes = json.dumps(payload).encode()

            # Sign with Falcon
            signature = falcon_sign_message(
                payload_bytes,
                self.falcon_private_key
            )

            # Create signed request for Fabric
            fabric_request = {
                "payload": payload,
                "signature": base64.b64encode(signature).decode('utf-8'),
                "publicKeyId": f"{self.org_id}_falcon"
            }

            # Make async HTTP request to Fabric gateway
            protocol = "https" if self.fabric_url.startswith("https") else "http"
            connector = aiohttp.TCPConnector(ssl=False if protocol == "http" else None)

            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                        f"{self.fabric_url}/api/chaincode/invoke",
                        json=fabric_request
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"Successfully updated session {session_id} in Fabric")
                    else:
                        logger.warning(f"Failed to update session in Fabric: {resp.status}")
                        response_text = await resp.text()
                        logger.warning(f"Response: {response_text}")
        except Exception as e:
            logger.error(f"Error forwarding to Fabric: {e}", exc_info=True)

    # HTTP Handlers

    async def handle_health(self, request):
        """Health check endpoint"""
        return web.json_response({
            'status': 'healthy',
            'node_id': self.node_id,
            'org_id': self.org_id,
            'peer_count': len(self.peers),
            'using_tls': request.url.scheme == 'https',
            'keys_available': {
                'falcon': self.falcon_public_key is not None,
                'kyber': self.kyber_public_key is not None
            }
        })

    async def handle_message(self, request):
        """Handle incoming message"""
        try:
            data = await request.json()

            # Check if this is a quantum-encrypted message
            if 'ciphertext' in data and 'encrypted_data' in data:
                logger.info(f"Received quantum-encrypted message from {data.get('org_id')}")
            else:
                # Regular message with sender info
                sender = data.get('sender', {})
                logger.info(f"Received message: {data.get('type')} from {sender.get('org_id')}")

            # Add to message queue for processing
            self.messages.append(data)

            return web.json_response({'status': 'received'})
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            return web.json_response({'error': str(e)}, status=400)

    async def handle_get_peers(self, request):
        """Get list of peers"""
        return web.json_response({
            'peers': [
                {
                    'node_id': node_id,
                    'org_id': peer['org_id'],
                    'url': peer['url'],
                    'last_seen': peer['last_seen'],
                    'has_quantum_keys': 'falcon_public' in peer and 'kyber_public' in peer
                }
                for node_id, peer in self.peers.items()
            ]
        })

    async def handle_register_peer(self, request):
        """Register a new peer"""
        try:
            data = await request.json()
            node_id = data.get('node_id')
            org_id = data.get('org_id')
            url = data.get('url')

            if node_id and org_id and url and node_id != self.node_id:
                protocol = "https" if url.startswith("https") else "http"
                self.peers[node_id] = {
                    'org_id': org_id,
                    'url': url,
                    'protocol': protocol,
                    'last_seen': time.time()
                }
                logger.info(f"Registered new peer: {org_id} ({node_id})")

                # Get peer's public keys
                await self.get_peer_public_keys(f"{url}/public_keys", node_id)

                return web.json_response({'status': 'registered'})
            else:
                logger.warning(f"Invalid peer registration request")
                return web.json_response({'error': 'Invalid peer data'}, status=400)
        except Exception as e:
            logger.error(f"Error registering peer: {e}")
            return web.json_response({'error': str(e)}, status=400)

    async def handle_get_node_id(self, request):
        """Get node ID"""
        return web.json_response({
            'node_id': self.node_id,
            'org_id': self.org_id
        })

    async def handle_get_public_keys(self, request):
        """Provide public quantum keys for peer-to-peer encryption"""
        try:
            # Convert Falcon public key to hex for transmission
            falcon_public_hex = None
            if self.falcon_public_key is not None:
                falcon_public_bytes = bytes(self.falcon_public_key)
                falcon_public_hex = falcon_public_bytes.hex()

            # Convert Kyber public key to hex for transmission
            kyber_public_hex = None
            if self.kyber_public_key is not None:
                kyber_public_bytes = bytes(self.kyber_public_key)
                kyber_public_hex = kyber_public_bytes.hex()

            return web.json_response({
                'org_id': self.org_id,
                'node_id': self.node_id,
                'falcon_public': falcon_public_hex,
                'kyber_public': kyber_public_hex
            })
        except Exception as e:
            logger.error(f"Error providing public keys: {e}")
            return web.json_response({'error': str(e)}, status=500)

    async def broadcast_key_rotation(self):
        """Broadcast key rotation notification to all peers"""
        try:
            # Rotate keys
            self._load_quantum_keys()

            # Prepare key rotation message
            message = {
                'type': 'key_rotation',
                'falcon_public': bytes(self.falcon_public_key).hex(),
                'kyber_public': bytes(self.kyber_public_key).hex(),
                'timestamp': time.time()
            }

            # Broadcast to all peers
            await self.broadcast_message(message)
            logger.info("Key rotation notification broadcasted")

        except Exception as e:
            logger.error(f"Failed to broadcast key rotation: {e}")


# Main entry point
async def main():
    # Get configuration from environment
    org_id = os.environ.get('ORG_ID', 'Hospital_A')
    peer_addresses = os.environ.get('PEER_ADDRESSES', '').split(',')
    fabric_url = os.environ.get('FABRIC_GATEWAY_URL', f'https://peer0.{org_id}.example.com:7051')

    # Filter out empty peer addresses
    peer_addresses = [addr for addr in peer_addresses if addr]

    # Create and start bridge
    bridge = QuantumLibP2PBridge(org_id, peer_addresses, fabric_url)
    app = await bridge.start()

    logger.info(f"LibP2P bridge for {org_id} started")

    # Keep running
    while True:
        # Periodically rotate keys (every 12 hours)
        await asyncio.sleep(43200)
        await bridge.broadcast_key_rotation()


# Entry point
if __name__ == "__main__":
    asyncio.run(main())