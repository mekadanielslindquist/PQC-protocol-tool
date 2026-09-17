import hashlib
import os
import json
import logging
import sys
from pathlib import Path
import requests
import time
from typing import Dict, Any, Optional
import base64
import uuid

# Set up paths
base_dir = Path(__file__).parent
sys.path.append(str(base_dir))
sys.path.append(str(base_dir / 'sip_connect'))

# Import quantum security modules
from sip_connect.hipaa_security import SecureKeyManager, HybridSecuritySystem
from sip_connect.key_utils import convert_to_ubyte_pointer, load_and_convert_keys

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Enable detailed logging
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('hedera_bridge.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('HederaBridge')


class HederaFabricBridge:
    """Bridge between Hedera Hashgraph and Hyperledger Fabric with quantum security"""

    def __init__(self, org_id: str):
        self.org_id = org_id

        # Use HTTPS by default for security
        self.wallet_service_url = os.environ.get('WALLET_SERVICE_URL', 'http://wallet-service:3000')
        self.fabric_gateway_url = os.environ.get('FABRIC_GATEWAY_URL', f'https://peer0.{org_id}.example.com:7051')

        # Initialize the secure key manager
        # SecureKeyManager (sip_connect/hipaa_security.py) is a pure
        # static-method utility class - every method on it takes org_id/
        # keys explicitly as arguments and none touch `self`, so it was
        # never meant to be instantiated (it has no __init__ that accepts
        # org_id, hence the old `SecureKeyManager(org_id)` call raising
        # "takes no arguments"). Hold the class itself; the static-method
        # calls below (self.secure_key_manager.sign_message(...)) work the
        # same way whether called via an instance or the class.
        self.secure_key_manager = SecureKeyManager

        # Load quantum keys
        self.quantum_keys = self._load_quantum_keys()

        # Setup request session with proper verification
        self.session = self._setup_request_session()

        # Initialize hybrid security system for additional encryption
        self._init_hybrid_security()

        logger.info(f"Initialized HederaFabricBridge for {org_id}")

    def _init_hybrid_security(self):
        """Initialize the hybrid security system for advanced encryption"""
        try:
            # Create a unique user key from org_id
            user_key = int(hashlib.sha256(self.org_id.encode()).hexdigest(), 16) % (2 ** 32)

            # Initialize from the sip_connect.hipaa_security module
            self.hybrid_security = HybridSecuritySystem(
                org_id=self.org_id,
                user_key=user_key,
                post_quantum=None,  # Will be initialized internally
                public_key=bytes(self.quantum_keys['falcon']['public']),
                private_key=bytes(self.quantum_keys['falcon']['private'])
            )

            logger.info(f"Initialized hybrid security system for {self.org_id}")
        except Exception as e:
            logger.error(f"Failed to initialize hybrid security: {e}")
            raise

    def _setup_request_session(self) -> requests.Session:
        """Set up a requests session with proper TLS configuration"""
        session = requests.Session()

        # Configure the session for TLS
        try:
            # Get certificate verification mode from environment
            verify_certs = os.environ.get("VERIFY_CERTS", "true").lower() == "true"

            if verify_certs:
                # Use system CA certificates by default
                # Or point to specific CA bundle if needed
                ca_bundle = os.environ.get("CA_BUNDLE_PATH", True)
                session.verify = ca_bundle
                logger.info(f"TLS certificate verification enabled: {ca_bundle}")
            else:
                # Disable certificate verification (not recommended for production)
                session.verify = False
                logger.warning("TLS certificate verification disabled - not secure for production")
                # Suppress insecure request warnings if verification is disabled
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            # Set default headers for all requests
            session.headers.update({
                "Content-Type": "application/json",
                "User-Agent": f"Quantum-Hedera-Bridge/{self.org_id}",
                "X-Organization-ID": self.org_id
            })

            # Set default timeout for all requests
            timeout = int(os.environ.get("REQUEST_TIMEOUT", "30"))
            session.timeout = timeout

            return session

        except Exception as e:
            logger.error(f"Failed to set up request session: {e}")
            raise

    def _load_quantum_keys(self) -> Dict[str, Any]:
        """Load quantum keys for the organization using the standardized method"""
        try:
            # Use the standardized key loading function if available
            try:
                converted_keys = load_and_convert_keys(self.org_id)

                return {
                    'falcon': {
                        'private': converted_keys['falcon_private'],
                        'public': converted_keys['falcon_public']
                    },
                    'kyber': {
                        'private': converted_keys['kyber_private'],
                        'public': converted_keys['kyber_public']
                    }
                }
            except (ImportError, AttributeError):
                # Fall back to original implementation if the function isn't available
                logger.warning("Falling back to direct key loading method")
                return self._load_quantum_keys_direct()

        except Exception as e:
            logger.error(f"Failed to load quantum keys: {e}")
            raise

    def _load_quantum_keys_direct(self) -> Dict[str, Any]:
        """Direct key loading as a fallback"""
        try:
            # Try to load keys from standard location
            keys_dir = Path(f'/app/keys')

            # Load Falcon keys
            falcon_private_path = keys_dir / f'{self.org_id}_falcon_private.key'
            falcon_public_path = keys_dir / f'{self.org_id}_falcon_public.key'

            # Load Kyber keys
            kyber_private_path = keys_dir / f'{self.org_id}_kyber_private.key'
            kyber_public_path = keys_dir / f'{self.org_id}_kyber_public.key'

            # Read key files
            with open(falcon_private_path, 'rb') as f:
                falcon_private = f.read()

            with open(falcon_public_path, 'rb') as f:
                falcon_public = f.read()

            with open(kyber_private_path, 'rb') as f:
                kyber_private = f.read()

            with open(kyber_public_path, 'rb') as f:
                kyber_public = f.read()

            # Convert to pointer format for the security library
            falcon_private_ptr = convert_to_ubyte_pointer(falcon_private)
            falcon_public_ptr = convert_to_ubyte_pointer(falcon_public)
            kyber_private_ptr = convert_to_ubyte_pointer(kyber_private)
            kyber_public_ptr = convert_to_ubyte_pointer(kyber_public)

            return {
                'falcon': {
                    'private': falcon_private_ptr,
                    'public': falcon_public_ptr
                },
                'kyber': {
                    'private': kyber_private_ptr,
                    'public': kyber_public_ptr
                }
            }

        except Exception as e:
            logger.error(f"Failed to load quantum keys directly: {e}")
            raise

    def _handle_response_error(self, response: requests.Response, context: str) -> None:
        """Handle error responses with detailed logging"""
        try:
            # Try to parse response as JSON
            error_data = response.json()
            error_message = error_data.get('message', error_data.get('error', 'Unknown error'))
            error_code = error_data.get('code', response.status_code)
            logger.error(
                f"{context} failed: Status {response.status_code}, Error code: {error_code}, Message: {error_message}")
        except ValueError:
            # Not JSON, log the raw text
            logger.error(f"{context} failed: Status {response.status_code}, Response: {response.text}")

    def submit_to_hedera(self, data: Dict[str, Any], use_enhanced_security: bool = True) -> str:
        """
        Submit data to Hedera Consensus Service with quantum signature

        Args:
            data: Dictionary of data to submit
            use_enhanced_security: Whether to use enhanced hybrid security (default: True)

        Returns:
            Hedera transaction ID
        """
        try:
            # Add request ID for tracking
            request_id = str(uuid.uuid4())
            logger.info(f"Starting Hedera submission {request_id}")

            # Convert data to bytes for signing
            data_bytes = json.dumps(data).encode()

            # Sign with Falcon
            signature = self.secure_key_manager.sign_message (
                data_bytes,
                self.quantum_keys['falcon']['private']
            )

            # Create payload for Hedera submission
            hedera_payload = {
                "data": data,
                "metadata": {
                    "orgId": self.org_id,
                    "timestamp": int(time.time()),
                    "dataHash": hashlib.sha256(data_bytes).hexdigest(),
                    "requestId": request_id
                },
                "signature": {
                    "algorithm": "falcon-1024",
                    "value": signature.hex(),
                    "publicKeyId": f"{self.org_id}_falcon"
                }
            }

            # Apply enhanced security if requested
            if use_enhanced_security:
                try:
                    # Encrypt the payload with hybrid security
                    encrypted_payload = self.hybrid_security.encrypt_message(hedera_payload)

                    # Create a wrapper payload with the encrypted data
                    wrapper_payload = {
                        "encryptedPayload": base64.b64encode(encrypted_payload).decode('utf-8'),
                        "algorithm": "quantum-hybrid",
                        "orgId": self.org_id,
                        "requestId": request_id
                    }
                    submission_payload = wrapper_payload
                    logger.debug("Using enhanced security for Hedera submission")
                except Exception as e:
                    logger.warning(f"Failed to apply enhanced security: {e}, falling back to basic security")
                    submission_payload = hedera_payload
            else:
                submission_payload = hedera_payload

            # Submit to wallet service that handles Hedera HCS
            response = self.session.post(
                f"{self.wallet_service_url}/api/consensus/submit",
                json=submission_payload
            )

            if response.status_code != 200:
                self._handle_response_error(response, "Hedera submission")
                raise Exception(f"Hedera submission failed: Status code {response.status_code}")

            # Extract transaction ID from response
            response_data = response.json()
            transaction_id = response_data.get("transactionId")

            if not transaction_id:
                logger.error(f"Missing transaction ID in Hedera response: {response_data}")
                raise Exception("Invalid Hedera response: Missing transaction ID")

            logger.info(f"Successfully submitted to Hedera: {transaction_id}")
            return transaction_id

        except requests.RequestException as e:
            logger.error(f"Network error submitting to Hedera: {e}")
            raise
        except Exception as e:
            logger.error(f"Error submitting to Hedera: {e}")
            raise

    def store_transaction_reference(self, transaction_id: str, reference_data: Dict[str, Any]) -> bool:
        """
        Store Hedera transaction reference in Hyperledger Fabric

        Args:
            transaction_id: Hedera transaction ID
            reference_data: Additional reference data to store

        Returns:
            Success status
        """
        try:
            # Generate request ID for tracking
            request_id = str(uuid.uuid4())
            logger.info(f"Storing transaction reference {request_id} for {transaction_id}")

            # Format data for chaincode
            chaincode_payload = {
                "function": "storeHederaReference",
                "args": [
                    transaction_id,
                    json.dumps(reference_data),
                    self.org_id
                ]
            }

            # Sign the chaincode request
            payload_bytes = json.dumps(chaincode_payload).encode()
            signature = self.secure_key_manager.sign_message(
                payload_bytes,
                self.quantum_keys['falcon']['private']
            )

            # Create signed request for Fabric
            fabric_request = {
                "payload": chaincode_payload,
                "signature": signature.hex(),
                "publicKeyId": f"{self.org_id}_falcon",
                "metadata": {
                    "requestId": request_id,
                    "timestamp": int(time.time())
                }
            }

            # Submit to Fabric peer
            response = self.session.post(
                f"{self.fabric_gateway_url}/api/chaincode/invoke",
                json=fabric_request
            )

            if response.status_code != 200:
                self._handle_response_error(response, "Fabric transaction storage")
                return False

            logger.info(f"Successfully stored Hedera transaction {transaction_id} in Fabric")
            return True

        except requests.RequestException as e:
            logger.error(f"Network error storing transaction in Fabric: {e}")
            return False
        except Exception as e:
            logger.error(f"Error storing transaction reference: {e}")
            return False

    def sync_hedera_to_fabric(self, topic_id: str, start_time: Optional[int] = None) -> int:
        """
        Sync messages from a Hedera topic to Fabric

        Args:
            topic_id: Hedera topic ID to sync
            start_time: Starting timestamp (in seconds since epoch)

        Returns:
            Count of synced messages
        """
        try:
            # Generate job ID for tracking
            job_id = str(uuid.uuid4())
            logger.info(f"Starting Hedera sync job {job_id} for topic {topic_id}")

            # Prepare request for wallet service
            params = {
                "topicId": topic_id,
                "jobId": job_id
            }
            if start_time:
                params["startTime"] = start_time

            # Add authentication headers if provided
            headers = {}
            api_key = os.environ.get('HEDERA_API_KEY')
            if api_key:
                headers['X-API-Key'] = api_key

            # Get messages from Hedera
            response = self.session.get(
                f"{self.wallet_service_url}/api/consensus/messages",
                params=params,
                headers=headers
            )

            if response.status_code != 200:
                self._handle_response_error(response, "Hedera message retrieval")
                return 0

            # Process response
            response_data = response.json()
            messages = response_data.get("messages", [])

            # Log summary of retrieved messages
            logger.info(f"Retrieved {len(messages)} messages from Hedera topic {topic_id}")
            if messages:
                first_msg = messages[0]
                last_msg = messages[-1]
                logger.debug(
                    f"First message: {first_msg.get('transactionId')} at {first_msg.get('consensusTimestamp')}")
                logger.debug(f"Last message: {last_msg.get('transactionId')} at {last_msg.get('consensusTimestamp')}")

            # Process and store each message in batches
            batch_size = int(os.environ.get('FABRIC_BATCH_SIZE', '10'))
            total_batches = (len(messages) + batch_size - 1) // batch_size
            synced_count = 0

            for batch_idx in range(total_batches):
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(messages))
                batch = messages[start_idx:end_idx]

                logger.debug(f"Processing batch {batch_idx + 1}/{total_batches} with {len(batch)} messages")

                # Process each message in the batch
                batch_success = 0
                for msg in batch:
                    transaction_id = msg.get("transactionId")
                    consensus_timestamp = msg.get("consensusTimestamp")

                    reference_data = {
                        "consensusTimestamp": consensus_timestamp,
                        "topicId": topic_id,
                        "sequenceNumber": msg.get("sequenceNumber"),
                        "messageHash": msg.get("messageHash"),
                        "syncJobId": job_id
                    }

                    if self.store_transaction_reference(transaction_id, reference_data):
                        batch_success += 1

                synced_count += batch_success
                logger.info(
                    f"Batch {batch_idx + 1}/{total_batches} complete: {batch_success}/{len(batch)} messages synced")

                # Small delay between batches to avoid overwhelming Fabric
                if batch_idx < total_batches - 1:
                    time.sleep(0.5)

            logger.info(f"Sync job {job_id} complete: {synced_count}/{len(messages)} messages synced to Fabric")
            return synced_count

        except requests.RequestException as e:
            logger.error(f"Network error during Hedera sync: {e}")
            return 0
        except Exception as e:
            logger.error(f"Error syncing from Hedera to Fabric: {e}")
            return 0

    def query_transaction(self, transaction_id: str) -> Dict[str, Any]:
        """
        Query transaction details from both Hedera and Fabric

        Args:
            transaction_id: Hedera transaction ID

        Returns:
            Combined transaction details
        """
        try:
            # Generate query ID for tracking
            query_id = str(uuid.uuid4())
            logger.info(f"Starting transaction query {query_id} for {transaction_id}")

            # Get transaction from Hedera
            hedera_response = self.session.get(
                f"{self.wallet_service_url}/api/consensus/transaction/{transaction_id}",
                params={"queryId": query_id}
            )

            if hedera_response.status_code != 200:
                logger.warning(f"Transaction not found in Hedera: {transaction_id}")
                hedera_data = None
            else:
                hedera_data = hedera_response.json()
                logger.debug(f"Retrieved Hedera data for transaction {transaction_id}")

            # Query from Fabric chaincode
            chaincode_payload = {
                "function": "getHederaReference",
                "args": [transaction_id]
            }

            # Sign the request (optional but adds security)
            payload_bytes = json.dumps(chaincode_payload).encode()
            signature = self.secure_key_manager.sign_message(
                payload_bytes,
                self.quantum_keys['falcon']['private']
            )

            # Add signature to request
            fabric_request = {
                "payload": chaincode_payload,
                "signature": signature.hex(),
                "publicKeyId": f"{self.org_id}_falcon",
                "metadata": {
                    "queryId": query_id
                }
            }

            fabric_response = self.session.post(
                f"{self.fabric_gateway_url}/api/chaincode/query",
                json=fabric_request
            )

            if fabric_response.status_code != 200:
                logger.warning(f"Transaction reference not found in Fabric: {transaction_id}")
                fabric_data = None
            else:
                fabric_data = fabric_response.json()
                logger.debug(f"Retrieved Fabric data for transaction {transaction_id}")

            # Combine the results
            result = {
                "transactionId": transaction_id,
                "hederaRecord": hedera_data,
                "fabricRecord": fabric_data,
                "foundIn": [],
                "queryTimestamp": int(time.time()),
                "queryId": query_id
            }

            if hedera_data:
                result["foundIn"].append("hedera")
            if fabric_data:
                result["foundIn"].append("fabric")

            logger.info(f"Query {query_id} complete: Transaction found in {', '.join(result['foundIn']) or 'none'}")
            return result

        except requests.RequestException as e:
            logger.error(f"Network error querying transaction: {e}")
            return {"error": str(e), "transactionId": transaction_id, "queryId": query_id}
        except Exception as e:
            logger.error(f"Error querying transaction {transaction_id}: {e}")
            return {"error": str(e), "transactionId": transaction_id, "queryId": query_id}

    def check_health(self) -> Dict[str, Any]:
        """
        Check health status of connected systems

        Returns:
            Health status information
        """
        health_info = {
            "bridge": {
                "status": "up",
                "org_id": self.org_id,
                "timestamp": int(time.time())
            },
            "hedera": {
                "status": "unknown",
                "lastChecked": int(time.time())
            },
            "fabric": {
                "status": "unknown",
                "lastChecked": int(time.time())
            }
        }

        # Check Hedera wallet service
        try:
            response = self.session.get(
                f"{self.wallet_service_url}/health",
                timeout=5
            )
            if response.status_code == 200:
                health_info["hedera"]["status"] = "up"
                health_info["hedera"]["details"] = response.json()
            else:
                health_info["hedera"]["status"] = "degraded"
                health_info["hedera"]["statusCode"] = response.status_code
        except Exception as e:
            health_info["hedera"]["status"] = "down"
            health_info["hedera"]["error"] = str(e)

        # Check Fabric gateway
        try:
            response = self.session.get(
                f"{self.fabric_gateway_url}/healthz",
                timeout=5
            )
            if response.status_code == 200:
                health_info["fabric"]["status"] = "up"
            else:
                health_info["fabric"]["status"] = "degraded"
                health_info["fabric"]["statusCode"] = response.status_code
        except Exception as e:
            health_info["fabric"]["status"] = "down"
            health_info["fabric"]["error"] = str(e)

        # Check quantum keys
        health_info["quantumKeys"] = {
            "falcon": self.quantum_keys["falcon"]["public"] is not None,
            "kyber": self.quantum_keys["kyber"]["public"] is not None
        }

        # Overall status
        if health_info["hedera"]["status"] == "up" and health_info["fabric"]["status"] == "up":
            health_info["status"] = "healthy"
        elif health_info["hedera"]["status"] == "down" and health_info["fabric"]["status"] == "down":
            health_info["status"] = "critical"
        else:
            health_info["status"] = "degraded"

        return health_info


# Example usage
if __name__ == "__main__":
    # Get organization ID from environment
    org_id = os.environ.get("ORG_ID", "Hospital_A")

    # Create bridge
    bridge = HederaFabricBridge(org_id)

    # Check system health
    health = bridge.check_health()
    print(f"System Health: {health['status']}")
    print(f"Hedera Status: {health['hedera']['status']}")
    print(f"Fabric Status: {health['fabric']['status']}")

    if health['status'] != 'healthy':
        print("Warning: System is not fully healthy. Check logs for details.")
        if sys.stdin.isatty():
            if input("Continue with sample transaction? (y/n): ").lower() != 'y':
                sys.exit(1)
        else:
            # No terminal attached (e.g. running as a docker-compose service) -
            # input() would immediately raise EOFError and crash the container
            # with exit 1 on every run. Skip the interactive sample-transaction
            # demo instead of blocking on stdin that will never arrive.
            print("No interactive terminal attached - skipping sample transaction.")
            sys.exit(0)

    # Example data
    sample_data = {
        "patientId": "anonymized_12345",
        "recordType": "medication",
        "timestamp": int(time.time()),
        "operation": "prescribe",
        "metadata": {
            "department": "cardiology",
            "authorized": True,
            "quantumSecured": True
        }
    }

    # Submit to Hedera
    try:
        transaction_id = bridge.submit_to_hedera(sample_data)
        print(f"Transaction submitted to Hedera: {transaction_id}")

        # Store reference in Fabric
        reference_data = {
            "dataType": "medication_record",
            "timestamp": sample_data["timestamp"],
            "department": sample_data["metadata"]["department"],
            "demo": True
        }

        success = bridge.store_transaction_reference(transaction_id, reference_data)
        print(f"Reference stored in Fabric: {success}")

        # Query the transaction to verify
        result = bridge.query_transaction(transaction_id)
        print(f"Transaction found in: {', '.join(result['foundIn'])}")

    except Exception as e:
        print(f"Error in sample submission: {e}")