import base64
import hashlib
import logging
import json
import uuid
from ctypes import POINTER
from ctypes import cast, c_ubyte
from datetime import datetime

from _datetime import timedelta
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from pathlib import Path

from sip_connect.key_utils import convert_to_ubyte_pointer
from sip_connect.falcon_wrapper import falcon_generate_keypair, falcon_sign_message, falcon_verify_signature, FALCON_SIGNATURE_SIZE, \
    FALCON_PRIVATE_KEY_SIZE, FALCON_PUBLIC_KEY_SIZE
from sip_connect.quantum_components import MelodyQuantumGenerator, EnvironmentalEntropy, DEFAULT_MELODY
from typing import Dict, Any, Optional, Union, Tuple

from sip_connect.kyber_wrapper import (kyber_keygen, kyber_decap, kyber_encap, KYBER_PUBLICKEYBYTES, KYBER_SECRETKEYBYTES)

logger = logging.getLogger('HybridSecuritySystem')

class HIPAACompliance:
    def __init__(self, organization_id: str):
        if not organization_id:
            raise ValueError("Organization ID cannot be empty")
        self.org_id = organization_id
        self.fernet = Fernet(Fernet.generate_key())
        self._setup_logging()

    def _setup_logging(self):
        log_path = Path(f'logs/hipaa_audit_{self.org_id}.log')
        log_path.parent.mkdir(exist_ok=True)
        logging.basicConfig(
            filename=str(log_path),
            level=logging.INFO,
            format='%(asctime)s:%(levelname)s:%(message)s'
        )

    def encrypt_phi(self, data: Dict[str, Any]) -> bytes:
        phi_json = json.dumps(data)
        return self.fernet.encrypt(phi_json.encode())

    def decrypt_phi(self, encrypted_data: bytes) -> Dict[str, Any]:
        decrypted = self.fernet.decrypt(encrypted_data)
        return json.loads(decrypted)

    def audit_log(self, action: str, user_id: str, data_accessed: str) -> None:
        logging.info(f"Org:{self.org_id}|Action:{action}|User:{user_id}|Data:{data_accessed}")


class AccessControl:
    def __init__(self):
        self.roles = {
            'doctor': ['read', 'write', 'update'],
            'nurse': ['read', 'update'],
            'admin': ['read'],
            'patient': ['read_own']
        }
        self.active_sessions: Dict[str, Dict] = {}

    def verify_access(self, user_id: str, role: str, action: str) -> bool:
        if not user_id or role not in self.roles:
            return False
        return action in self.roles[role]

    def create_session(self, user_id: str, role: str) -> str:
        if not user_id:
            raise ValueError("User ID cannot be empty")

        # Ensure user_id is a string and stripped of whitespace
        user_id = str(user_id).strip()

        # Generate session ID using a more robust method
        session_id = hashlib.sha256(
            f"{user_id}{role}{datetime.now()}".encode('utf-8')
        ).hexdigest()

        # Store session with full details
        self.active_sessions[session_id] = {
            'user_id': user_id,  # Ensure this is exactly the same user ID used for creation
            'role': role,
            'created': datetime.now()
        }

        print(f"Session created for user: {user_id}")
        print(f"Session ID: {session_id}")

        return session_id


class MessageIntegrity:
    def __init__(self, cert_dir: str = 'certificates'):
        self.cert_dir = Path(cert_dir)
        self.cert_dir.mkdir(exist_ok=True)

    def verify_signature(self, message: bytes, signature: bytes, public_key: bytes) -> bool:
        # Existing implementation looks good, but add explicit key validation
        if not self._validate_key(public_key):
            logging.error("Invalid public key format")
            return False
        """Verify a Falcon signature."""
        try:
            # Truncate signature if it's too long
            if len(signature) > FALCON_SIGNATURE_SIZE:
                signature = signature[:FALCON_SIGNATURE_SIZE]
                logging.warning(f"Truncated signature to maximum size {FALCON_SIGNATURE_SIZE}")

            # Using falcon_verify_signature to verify the signature
            if not falcon_verify_signature(message, signature, public_key):
                logging.error(f"Falcon signature verification failed")
                return False
            logging.info(f"Signature verification successful")
            return True
        except Exception as e:
            logging.error(f"Signature verification failed: {str(e)}")
            return False

    def _validate_key(self, public_key: bytes) -> bool:
        """Validate key format and basic properties"""
        return (
                isinstance(public_key, bytes) and
                0 < len(public_key) <= FALCON_PUBLIC_KEY_SIZE
        )

    def sign_message(self, message: bytes, private_key) -> bytes:
        return SecureKeyManager.sign_message(message, private_key)


class KeyManagement:
    def __init__(self, key_dir: str = 'keys'):
        self.key_dir = Path(key_dir)
        self.key_dir.mkdir(exist_ok=True)

    def get_private_key(self, org_id: str) -> Optional[Any]:
        try:
            key_path = self.key_dir / f'{org_id}_falcon_private_key.pem'
            if not key_path.exists():
                logger.error(f"Private key not found for organization {org_id}")
                return None

            with open(key_path, 'rb') as key_file:
                private_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=None
                )
            return private_key
        except Exception as e:
            logger.error(f"Error loading private key: {str(e)}")
            return None

    def generate_and_save_keypair(self, org_id: str):
        """New method to generate and save Falcon keypair"""
        try:
            public_key, private_key = falcon_generate_keypair()

            # Ensure keys directory exists
            self.key_dir.mkdir(exist_ok=True)

            # Save public key
            public_key_path = self.key_dir / f'{org_id}_falcon_public.key'
            with open(public_key_path, 'wb') as f:
                f.write(public_key)

            # Save private key
            private_key_path = self.key_dir / f'{org_id}_falcon_private.key'
            with open(private_key_path, 'wb') as f:
                f.write(private_key)

            logger.info(f"Generated and saved Falcon keypair for {org_id}")
            return public_key, private_key

        except Exception as e:
            logger.error(f"Failed to generate keypair for {org_id}: {str(e)}")
            raise


class PostQuantumSessionSecurity:
    def __init__(self, falcon_public_key: Union[str, bytes], falcon_private_key: Union[str, bytes],
                 kyber_public_key: Union[str, bytes] = None, kyber_private_key: Union[str, bytes] = None):
        """Initialize with both Falcon and Kyber keys."""
        # Initialize active_sessions
        self.active_sessions = {}

        # Initialize quantum components
        self.quantum_gen = MelodyQuantumGenerator(DEFAULT_MELODY)
        self.entropy = EnvironmentalEntropy()

        # Process Falcon keys
        self.falcon_public_key = self._ensure_bytes(falcon_public_key)
        self.falcon_private_key = self._ensure_bytes(falcon_private_key)

        # Process Kyber keys or generate new ones
        if kyber_public_key and kyber_private_key:
            self.kyber_public_key = self._ensure_bytes(kyber_public_key)
            self.kyber_private_key = self._ensure_bytes(kyber_private_key)
        else:
            self.kyber_public_key, self.kyber_private_key = kyber_keygen()

        # Add validation for both key pairs
        self._validate_keypairs()

    def _ensure_bytes(self, key: Union[str, bytes]) -> bytes:
        """Ensure key is in bytes format"""
        return key.encode('utf-8') if isinstance(key, str) else key

    def _validate_keypairs(self):
        """Validate both Falcon and Kyber key pairs"""
        self._validate_falcon_keypair()
        self._validate_kyber_keypair()

    def _validate_falcon_keypair(self):
        """Validate the Falcon key pair"""
        if not isinstance(self.falcon_public_key, bytes) or not isinstance(self.falcon_private_key, bytes):
            raise ValueError("Falcon keys must be in bytes format")

        # Verify Falcon key sizes
        if len(self.falcon_private_key) != FALCON_PRIVATE_KEY_SIZE:
            raise ValueError(f"Invalid Falcon private key size: {len(self.falcon_private_key)}")
        if len(self.falcon_public_key) != FALCON_PUBLIC_KEY_SIZE:
            raise ValueError(f"Invalid Falcon public key size: {len(self.falcon_public_key)}")

        # Verify key can be used for signing and verification
        test_message = b"Falcon Validation Test"
        try:
            signature = falcon_sign_message(test_message, self.falcon_private_key)
            if not falcon_verify_signature(test_message, signature, self.falcon_public_key):
                raise ValueError("Falcon key pair validation failed")
        except Exception as e:
            raise ValueError(f"Invalid Falcon key pair: {str(e)}")

    def _validate_kyber_keypair(self):
        """Validate the Kyber key pair"""
        if not isinstance(self.kyber_public_key, bytes) or not isinstance(self.kyber_private_key, bytes):
            raise ValueError("Kyber keys must be in bytes format")

        # Test Kyber encryption/decryption
        try:
            test_shared_secret, test_ciphertext = kyber_encap(self.kyber_public_key)
            decapped_secret = kyber_decap(test_ciphertext, self.kyber_private_key)
            if test_shared_secret != decapped_secret:
                raise ValueError("Kyber key pair validation failed")
        except Exception as e:
            raise ValueError(f"Invalid Kyber key pair: {str(e)}")

    def create_session(self, user_id: str, role: str, duration: int = 3600) -> dict:
        """Create a basic session with session ID and expiration"""
        session_id = hashlib.sha3_256(
            f"{user_id}{role}{datetime.now().isoformat()}".encode()
        ).hexdigest()

        session_data = {
            "user_id": user_id,
            "role": role,
            "expires": datetime.now() + timedelta(seconds=duration),
            "session_id": session_id,
            "created_at": datetime.now().isoformat()
        }

        self.active_sessions[session_id] = session_data
        return session_data

    def create_quantum_enhanced_session(self, user_id: str, role: str, duration: int = 3600) -> dict:
        """Create a quantum-enhanced session with both Falcon and Kyber security"""
        try:
            # Create base session
            session = self.create_session(user_id, role, duration)

            # Generate quantum components
            quantum_key = self.quantum_gen.generate_quantum_key()
            entropy_data = self.entropy.get_astronomical_data()

            # Create Kyber encryption for session
            shared_secret, ciphertext = kyber_encap(self.kyber_public_key)

            # Combine quantum and classical entropy
            session_key = hashlib.sha256(
                shared_secret +
                str(quantum_key).encode() +
                json.dumps(entropy_data).encode()
            ).digest()

            # Add quantum and post-quantum components to session
            session.update({
                'entropy_data': entropy_data,
                'quantum_key': str(quantum_key),
                'kyber_ciphertext': base64.b64encode(ciphertext).decode('utf-8'),
                'session_key_hash': hashlib.sha256(session_key).hexdigest()
            })

            # Sign session data with Falcon
            message = json.dumps({
                'session_id': session['session_id'],
                'user_id': user_id,
                'role': role,
                'quantum_key': session['quantum_key'],
                'session_key_hash': session['session_key_hash']
            }).encode('utf-8')

            session['falcon_signature'] = base64.b64encode(
                falcon_sign_message(message, self.falcon_private_key)
            ).decode('utf-8')

            return session

        except Exception as e:
            logger.error(f"Failed to create quantum session: {str(e)}")
            raise

    def verify_session(self, session_id: str) -> bool:
        """Verify a session's validity and integrity"""
        try:
            if session_id not in self.active_sessions:
                return False

            session = self.active_sessions[session_id]

            # Check expiration
            if datetime.now() > session['expires']:
                del self.active_sessions[session_id]
                return False

            # Verify Falcon signature if it exists
            if 'falcon_signature' in session:
                message = json.dumps({
                    'session_id': session['session_id'],
                    'user_id': session['user_id'],
                    'role': session['role'],
                    'quantum_key': session['quantum_key'],
                    'session_key_hash': session['session_key_hash']
                }).encode('utf-8')

                signature = base64.b64decode(session['falcon_signature'])

                if not falcon_verify_signature(message, signature, self.falcon_public_key):
                    return False

            return True

        except Exception as e:
            logger.error(f"Session verification failed: {str(e)}")
            return False

    def refresh_session(self, session_id: str, duration: int = 3600) -> dict:
        """Refresh a session with new quantum entropy and signatures"""
        try:
            if not self.verify_session(session_id):
                raise ValueError("Invalid or expired session")

            session = self.active_sessions[session_id]

            # Update expiration
            session['expires'] = datetime.now() + timedelta(seconds=duration)

            # Generate new quantum components
            quantum_key = self.quantum_gen.generate_quantum_key()
            entropy_data = self.entropy.get_astronomical_data()

            # Generate new Kyber encryption
            shared_secret, ciphertext = kyber_encap(self.kyber_public_key)

            # Create new session key
            session_key = hashlib.sha256(
                shared_secret +
                str(quantum_key).encode() +
                json.dumps(entropy_data).encode()
            ).digest()

            # Update session with new data
            session.update({
                'entropy_data': entropy_data,
                'quantum_key': str(quantum_key),
                'kyber_ciphertext': base64.b64encode(ciphertext).decode('utf-8'),
                'session_key_hash': hashlib.sha256(session_key).hexdigest(),
                'refreshed_at': datetime.now().isoformat()
            })

            # Create new Falcon signature
            message = json.dumps({
                'session_id': session['session_id'],
                'user_id': session['user_id'],
                'role': session['role'],
                'quantum_key': session['quantum_key'],
                'session_key_hash': session['session_key_hash']
            }).encode('utf-8')

            session['falcon_signature'] = base64.b64encode(
                falcon_sign_message(message, self.falcon_private_key)
            ).decode('utf-8')

            return session

        except Exception as e:
            logger.error(f"Session refresh failed: {str(e)}")
            raise

    def revoke_session(self, session_id: str) -> bool:
        """Revoke a session"""
        try:
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
                return True
            return False
        except Exception as e:
            logger.error(f"Session revocation failed: {str(e)}")
            return False


class HybridSecuritySystem:
    def __init__(self, org_id: str, user_key: int, post_quantum: 'PostQuantumSessionSecurity',
                 public_key: bytes = None, private_key: bytes = None):
        """Initialize hybrid security system with quantum and classical cryptography."""
        self.org_id = org_id
        self.user_key = user_key
        self.post_quantum = post_quantum
        self.active_sessions = {}

        # Initialize quantum components
        self.quantum_gen = MelodyQuantumGenerator(DEFAULT_MELODY)
        self.entropy = EnvironmentalEntropy()

        try:
            if public_key and private_key:
                # Use provided Falcon keys and generate new Kyber keys
                self.falcon_public_key = public_key
                self.falcon_private_key = private_key
                kyber_keys = SecureKeyManager.generate_kyber_keypair(org_id)
                self.kyber_public_key = bytes(kyber_keys['public_key'])
                self.kyber_private_key = bytes(kyber_keys['private_key'])
            else:
                # Generate both key pairs
                keys = SecureKeyManager.generate_all_keys(org_id)
                self.falcon_public_key = keys['falcon']['public_key']
                self.falcon_private_key = keys['falcon']['private_key']
                self.kyber_public_key = keys['kyber']['public_key']
                self.kyber_private_key = keys['kyber']['private_key']

            # Validate both key pairs
            self._validate_keys()

        except Exception as e:
            logger.error(f"Failed to initialize {org_id} security system: {str(e)}")
            raise

    def _validate_keys(self):
        """Validate both Falcon and Kyber key pairs."""
        self._validate_falcon_keys()
        self._validate_kyber_keys()
        logger.info("Both Falcon and Kyber key pairs validated successfully")

    def _validate_falcon_keys(self):
        """Validate Falcon key pair."""
        # Validate key sizes
        if len(self.falcon_private_key) != FALCON_PRIVATE_KEY_SIZE:
            raise ValueError(
                f"Invalid Falcon private key size: {len(self.falcon_private_key)}.")
        if len(self.falcon_public_key) != FALCON_PUBLIC_KEY_SIZE:
            raise ValueError(
                f"Invalid Falcon public key size: {len(self.falcon_public_key)}.")

        # Verify keys work together
        test_message = b"Falcon Key Validation Test"
        try:
            signature = falcon_sign_message(test_message, self.falcon_private_key)
            if not falcon_verify_signature(test_message, signature, self.falcon_public_key):
                raise ValueError("Falcon key pair validation failed")
            logger.debug("Falcon key pair validated successfully")
        except Exception as e:
            raise ValueError(f"Falcon key validation error: {str(e)}")

    def _validate_kyber_keys(self):
        """Validate Kyber key pair."""
        try:
            # Convert Kyber public/private keys to proper format
            kyber_public_ptr = cast((c_ubyte * KYBER_PUBLICKEYBYTES).from_buffer_copy(self.kyber_public_key),
                                    POINTER(c_ubyte))
            kyber_private_ptr = cast((c_ubyte * KYBER_SECRETKEYBYTES).from_buffer_copy(self.kyber_private_key),
                                     POINTER(c_ubyte))

            test_shared_secret, test_ciphertext = kyber_encap(kyber_public_ptr)
            decapped_secret = kyber_decap(test_ciphertext, kyber_private_ptr)

            # Verify the shared secrets match
            if test_shared_secret != decapped_secret:
                raise ValueError("Kyber key pair validation failed - shared secrets don't match")

            logger.debug("Kyber key pair validated successfully")
        except Exception as e:
            raise ValueError(f"Kyber key validation error: {str(e)}")

    def _generate_hybrid_key(self) -> bytes:
        """Generate encryption key using both quantum and classical entropy."""
        try:
            quantum_key = self.quantum_gen.generate_quantum_key()
            env_entropy = self.entropy.generate_entropy()
            astro_data = self.entropy.get_astronomical_data()

            # Include both Kyber and Falcon public keys in entropy sources
            entropy_sources = [
                str(quantum_key).encode(),
                str(env_entropy).encode(),
                json.dumps(astro_data, sort_keys=True).encode(),
                str(self.user_key).encode(),
                str(uuid.uuid4()).encode(),
                self.falcon_public_key[:32],  # Use first 32 bytes of each key
                self.kyber_public_key[:32]  # for additional entropy
            ]

            hybrid_key = hashlib.sha256(b''.join(entropy_sources)).digest()
            logger.debug("Generated hybrid key successfully")
            return hybrid_key
        except Exception as e:
            logger.error(f"Failed to generate hybrid key: {e}")
            raise

    def encrypt_message(self, message: Union[Dict[str, Any], bytes]) -> bytes:
        try:
            logger.debug("=== Starting Encryption Process ===")

            # Convert message to bytes
            if isinstance(message, dict):
                message_bytes = json.dumps(message).encode('utf-8')
            elif isinstance(message, str):
                message_bytes = message.encode('utf-8')
            elif isinstance(message, bytes):
                message_bytes = message
            else:
                raise ValueError(f"Unsupported message type: {type(message)}")

            # Generate encryption components using Kyber
            kyber_public_ptr = cast((c_ubyte * KYBER_PUBLICKEYBYTES).from_buffer_copy(self.kyber_public_key),
                                    POINTER(c_ubyte))
            shared_secret, ciphertext = kyber_encap(kyber_public_ptr)

            # Generate encryption key from shared secret and quantum entropy
            encryption_key = hashlib.sha256(
                shared_secret + self._generate_hybrid_key()
            ).digest()

            # Encrypt the data
            fernet = Fernet(base64.urlsafe_b64encode(encryption_key))
            encrypted_data = fernet.encrypt(message_bytes)

            # Sign with Falcon
            falcon_signature = falcon_sign_message(encrypted_data, self.falcon_private_key)

            # Create package with both Kyber and Falcon components
            package = {
                'encrypted_data': base64.b64encode(encrypted_data).decode('utf-8'),
                'kyber_ciphertext': base64.b64encode(ciphertext).decode('utf-8'),
                'falcon_signature': base64.b64encode(falcon_signature).decode('utf-8'),
                'metadata': {
                    'org_id': self.org_id,
                    'timestamp': datetime.now().isoformat(),
                    'entropy': str(self.entropy.generate_entropy()),
                    'kyber_public_hash': hashlib.sha256(self.kyber_public_key).hexdigest()[:16],
                    'falcon_public_hash': hashlib.sha256(self.falcon_public_key).hexdigest()[:16]
                }
            }

            return json.dumps(package).encode('utf-8')

        except Exception as e:
            logger.error(f"Encryption failed: {str(e)}")
            raise

    def decrypt_message(self, encrypted_package: bytes) -> Dict[str, Any]:
        try:
            logger.debug("=== Starting Decryption Process ===")

            # Parse package
            package = json.loads(encrypted_package.decode('utf-8'))

            # Decode components
            encrypted_data = base64.b64decode(package['encrypted_data'])
            kyber_ciphertext = base64.b64decode(package['kyber_ciphertext'])
            falcon_signature = base64.b64decode(package['falcon_signature'])

            # Verify Falcon signature first
            if not falcon_verify_signature(encrypted_data, falcon_signature, self.falcon_public_key):
                raise ValueError("Invalid Falcon signature")

            # Recover shared secret using Kyber
            kyber_private_ptr = cast((c_ubyte * KYBER_SECRETKEYBYTES).from_buffer_copy(self.kyber_private_key),
                                     POINTER(c_ubyte))
            shared_secret = kyber_decap(kyber_ciphertext, kyber_private_ptr)

            # Derive encryption key using both shared secret and quantum entropy
            encryption_key = hashlib.sha256(
                shared_secret + self._generate_hybrid_key()
            ).digest()

            # Decrypt data
            fernet = Fernet(base64.urlsafe_b64encode(encryption_key))
            decrypted_data = fernet.decrypt(encrypted_data)

            # Verify the decrypted data structure
            try:
                result = json.loads(decrypted_data.decode('utf-8'))
                logger.debug("Successfully decrypted and parsed data")
                return result
            except json.JSONDecodeError:
                logger.debug("Decrypted data is not JSON, returning as bytes")
                return {'raw_data': decrypted_data}

        except Exception as e:
            logger.error(f"Decryption failed: {str(e)}")
            raise

    def rotate_keys(self) -> Dict[str, Dict[str, bytes]]:
        """Rotate both Kyber and Falcon keys."""
        try:
            logger.info(f"Starting key rotation for {self.org_id}")

            # Generate new keys
            new_keys = SecureKeyManager.generate_all_keys(self.org_id)

            # Update instance variables
            self.falcon_public_key = new_keys['falcon']['public_key']
            self.falcon_private_key = new_keys['falcon']['private_key']
            self.kyber_public_key = bytes(new_keys['kyber']['public_key'])
            self.kyber_private_key = bytes(new_keys['kyber']['private_key'])

            # Validate new keys
            self._validate_keys()

            logger.info(f"Successfully rotated keys for {self.org_id}")
            return new_keys

        except Exception as e:
            logger.error(f"Key rotation failed: {str(e)}")
            raise

    def verify_message_integrity(self, message: Dict[str, Any]) -> bool:
        """Verify the integrity of a message using both Kyber and Falcon."""
        try:
            # Check required fields
            required_fields = ['encrypted_data', 'kyber_ciphertext', 'falcon_signature', 'metadata']
            if not all(field in message for field in required_fields):
                return False

            # Verify metadata
            metadata = message.get('metadata', {})
            if not all(k in metadata for k in ['org_id', 'timestamp', 'entropy']):
                return False

            # Verify signatures and ciphertexts are valid base64
            try:
                encrypted_data = base64.b64decode(message['encrypted_data'])
                kyber_ciphertext = base64.b64decode(message['kyber_ciphertext'])
                falcon_signature = base64.b64decode(message['falcon_signature'])
            except Exception as e:
                logger.error(f"cipertexts aren't valid base64: {str(e)}")
                return False

            # Verify Falcon signature
            if not falcon_verify_signature(encrypted_data, falcon_signature, kyber_ciphertext):
                return False

            return True

        except Exception as e:
            logger.error(f"Message integrity verification failed: {str(e)}")
            return False


class SecureKeyManager:
    @staticmethod
    def generate_falcon_keypair(org_id: str, key_dir: str = 'keys') -> Dict[str, bytes]:
        """
        Generate and save Falcon keypair with standardized storage.

        Args:
            org_id: Unique identifier for the organization
            key_dir: Directory to store keys (default: 'keys')

        Returns:
            Dictionary containing public and private keys with their file paths
        """
        try:
            # Generate Falcon keypair
            public_key, private_key = falcon_generate_keypair()

            # Ensure keys directory exists
            keys_dir = Path(key_dir)
            keys_dir.mkdir(exist_ok=True)

            # Define key file paths
            public_key_path = keys_dir / f'{org_id}_falcon_public.key'
            private_key_path = keys_dir / f'{org_id}_falcon_private.key'

            # Save public key
            with open(public_key_path, 'wb') as f:
                f.write(public_key)

            # Save private key
            with open(private_key_path, 'wb') as f:
                f.write(private_key)

            # Validate the generated keypair
            if not SecureKeyManager.validate_key_pair(public_key, private_key):
                raise ValueError("Generated key pair failed validation")

            return {
                'public_key': public_key,
                'private_key': private_key,
                'public_key_path': str(public_key_path),
                'private_key_path': str(private_key_path)
            }

        except Exception as e:
            logger.error(f"Keypair generation failed for {org_id}: {str(e)}")
            raise

    @staticmethod
    def generate_kyber_keypair(org_id: str, key_dir: str = 'keys') -> Dict[str, bytes]:
        """
        Generate and save Kyber keypair.

        Args:
            org_id: Unique identifier for the organization
            key_dir: Directory to store keys (default: 'keys')

        Returns:
            Dictionary containing public and private keys with their file paths
        """
        try:
            # Generate Kyber keypair
            public_key, private_key = kyber_keygen()

            # Ensure keys directory exists
            keys_dir = Path(key_dir)
            keys_dir.mkdir(exist_ok=True)

            # Define key file paths
            public_key_path = keys_dir / f'{org_id}_kyber_public.key'
            private_key_path = keys_dir / f'{org_id}_kyber_private.key'

            # Save public key
            with open(public_key_path, 'wb') as f:
                f.write(public_key)

            # Save private key
            with open(private_key_path, 'wb') as f:
                f.write(private_key)

            return {
                'public_key': public_key,
                'private_key': private_key,
                'public_key_path': str(public_key_path),
                'private_key_path': str(private_key_path)
            }

        except Exception as e:
            logger.error(f"Kyber keypair generation failed for {org_id}: {str(e)}")
            raise

    @staticmethod
    def load_keys(org_id: str, key_dir: str = 'keys', key_type: str = 'falcon') -> Dict[str, bytes]:
        """
        Load keys for a specific organization with validation.

        Args:
            org_id: Unique identifier for the organization
            key_dir: Directory storing keys (default: 'keys')
            key_type: Type of keys to load ('falcon' or 'kyber')

        Returns:
            Dictionary containing public and private keys
        """
        keys_dir = Path(key_dir)

        # Set file names based on key type
        if key_type.lower() == 'falcon':
            public_key_path = keys_dir / f'{org_id}_falcon_public.key'
            private_key_path = keys_dir / f'{org_id}_falcon_private.key'
        elif key_type.lower() == 'kyber':
            public_key_path = keys_dir / f'{org_id}_kyber_public.key'
            private_key_path = keys_dir / f'{org_id}_kyber_private.key'
        else:
            raise ValueError(f"Unsupported key type: {key_type}")

        # Check if key files exist
        if not public_key_path.exists() or not private_key_path.exists():
            raise FileNotFoundError(f"{key_type} keys for {org_id} not found")

        # Read keys
        with open(public_key_path, 'rb') as f:
            public_key = f.read()

        with open(private_key_path, 'rb') as f:
            private_key = f.read()

        # Validate key pair if it's Falcon
        if key_type.lower() == 'falcon' and not SecureKeyManager.validate_key_pair(public_key, private_key):
            raise ValueError(f"Invalid Falcon key pair for {org_id}")

        return {
            'public_key': public_key,
            'private_key': private_key
        }

    @staticmethod
    def generate_all_keys(org_id: str, key_dir: str = 'keys') -> Dict[str, Dict[str, bytes]]:
        """
        Generate and save both Falcon and Kyber keypairs.

        Args:
            org_id: Unique identifier for the organization
            key_dir: Directory to store keys (default: 'keys')

        Returns:
            Dictionary containing both keypairs
        """
        try:
            falcon_keys = SecureKeyManager.generate_falcon_keypair(org_id, key_dir)
            kyber_keys = SecureKeyManager.generate_kyber_keypair(org_id, key_dir)

            return {
                'falcon': falcon_keys,
                'kyber': kyber_keys
            }
        except Exception as e:
            logger.error(f"Failed to generate all keys for {org_id}: {str(e)}")
            raise

    @staticmethod
    def sign_message(message: bytes, private_key: bytes) -> bytes:
        """Sign a message using Falcon signature"""
        try:
            signature = falcon_sign_message(message, private_key)

            # Truncate signature if it exceeds maximum size
            if len(signature) > FALCON_SIGNATURE_SIZE:
                signature = signature[:FALCON_SIGNATURE_SIZE]
                logger.warning(f"Truncated signature to maximum size {FALCON_SIGNATURE_SIZE}")

            return signature

        except Exception as e:
            logger.error(f"Falcon message signing failed: {str(e)}")
            raise ValueError(f"Message signing error: {str(e)}")

    @staticmethod
    def validate_key_pair(public_key: bytes, private_key: bytes) -> bool:
        """Validate that a Falcon public-private key pair works together."""
        test_message = b"Validation Test Message"
        try:
            signature = falcon_sign_message(test_message, private_key)
            return falcon_verify_signature(test_message, signature, public_key)
        except Exception as e:
            logger.error(f"Key pair validation failed: {str(e)}")
            return False


class EnhancedEncryption:
    def __init__(self, user_key: int, melody_sequence=None, org_id="Hospital_A", shared_keys=None):
        """Initialize EnhancedEncryption with required components."""
        # Initialize quantum components
        self.quantum_gen = MelodyQuantumGenerator(melody_sequence or DEFAULT_MELODY)
        self.entropy = EnvironmentalEntropy()

        # Initialize security components
        self.user_key = user_key
        self.org_id = org_id

        # Initialize keys
        self.public_key = None
        self.private_key = None

        # Load keys either from shared keys or from files
        if shared_keys and isinstance(shared_keys, dict):
            self.public_key = shared_keys.get('public_key')
            self.private_key = shared_keys.get('private_key')
        else:
            self._load_falcon_keys()

    def _load_falcon_keys(self):
        """Load Falcon keys from the keys directory."""
        try:
            keys_dir = Path('keys')
            if not keys_dir.exists():
                raise FileNotFoundError(f"Keys directory '{keys_dir}' does not exist.")

            # Load public key
            public_key_path = keys_dir / f'{self.org_id}_falcon_public.key'
            if public_key_path.exists():
                with open(public_key_path, 'rb') as f:
                    self.public_key = f.read()
            else:
                raise FileNotFoundError(f"Public key file '{public_key_path}' not found.")

            # Load private key
            private_key_path = keys_dir / f'{self.org_id}_falcon_private.key'
            if private_key_path.exists():
                with open(private_key_path, 'rb') as f:
                    self.private_key = f.read()
            else:
                raise FileNotFoundError(f"Private key file '{private_key_path}' not found.")

        except Exception as e:
            raise Exception(f"Failed to load Falcon keys for '{self.org_id}': {str(e)}")

    @staticmethod
    def verify_message(message, signature, public_key):
        logger.debug(f"Verifying Message: {message}")
        logger.debug(f"Falcon Signature: {signature.hex()}")
        is_valid = falcon_verify_signature(message, signature, public_key)
        logger.debug(f"Verification Result: {is_valid}")
        return is_valid

    @staticmethod
    def sign_message(message, private_key):
        logger.debug(f"Signing Message: {message}")  # BEFORE signing
        signature = falcon_sign_message(message, private_key)
        logger.debug(f"Generated Falcon Signature: {signature.hex()}")
        return signature

    def encrypt(self, message: Union[Dict[str, Any], str, bytes]) -> bytes:
        try:
            logger.debug("=== Starting Encryption Process ===")
            logger.debug(f"Input message type: {type(message)}")

            # Generate encryption components
            quantum_key = self.quantum_gen.generate_quantum_key()
            logger.debug(f"Generated quantum key: {quantum_key}")
            env_entropy = self.entropy.get_astronomical_data()
            logger.debug(f"Generated entropy: {json.dumps(env_entropy, indent=2)}")

            # Convert message
            if isinstance(message, dict):
                message_bytes = json.dumps(message).encode('utf-8')
                logger.debug(f"Converted dict to bytes, length: {len(message_bytes)}")
                logger.debug(f"Original dict keys: {message.keys()}")
            elif isinstance(message, str):
                message_bytes = message.encode('utf-8')
                logger.debug(f"Converted string to bytes, length: {len(message_bytes)}")
            elif isinstance(message, bytes):
                message_bytes = message
                logger.debug(f"Using raw bytes, length: {len(message_bytes)}")
            else:
                raise ValueError(f"Unsupported message type: {type(message)}")

            # Generate signature first
            logger.debug("Generating Falcon signature...")
            logger.debug(f"Private key length: {len(self.private_key)}")
            signature = self.sign_message(message_bytes, self.private_key)
            logger.debug(f"Generated signature length: {len(signature)}")
            logger.debug(f"Signature hex: {signature.hex()[:100]}...")  # First 100 chars

            # Generate encryption key
            encryption_key = hashlib.sha256(
                b''.join([
                    str(self.user_key).encode(),
                    str(quantum_key).encode(),
                    json.dumps(env_entropy, sort_keys=True).encode()
                ])
            ).digest()
            logger.debug(f"Generated encryption key length: {len(encryption_key)}")

            # Encrypt data
            cipher = Fernet(base64.urlsafe_b64encode(encryption_key))
            encrypted_data = cipher.encrypt(message_bytes)
            logger.debug(f"Encrypted data length: {len(encrypted_data)}")

            # Create package
            package = {
                'encrypted_data': base64.b64encode(encrypted_data).decode('utf-8'),
                'falcon_signature': base64.b64encode(signature).decode('utf-8'),
                'metadata': {
                    'org_id': self.org_id,
                    'timestamp': datetime.now().isoformat(),
                    'quantum_key': str(quantum_key),
                    'entropy': base64.b64encode(json.dumps(env_entropy).encode()).decode('utf-8')
                }
            }
            logger.debug("=== Package Structure ===")
            logger.debug(f"Package keys: {list(package.keys())}")
            logger.debug(f"Metadata keys: {list(package['metadata'].keys())}")

            return json.dumps(package).encode('utf-8')

        except Exception as e:
            logger.error(f"Encryption failed: {str(e)}")
            logger.exception("Full encryption error traceback:")
            raise

    def decrypt(self, encrypted_package: bytes) -> Union[Dict[str, Any], bytes]:
        try:
            logger.debug("=== Starting Decryption Process ===")
            logger.debug(f"Input package length: {len(encrypted_package)}")

            # Parse package and validate structure first
            try:
                package = json.loads(encrypted_package.decode('utf-8'))
                logger.debug(f"Successfully parsed JSON package")
                logger.debug(f"Raw package contents: {package}")  # Let's see exactly what we got
                logger.debug(f"Package keys available: {list(package.keys())}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode JSON package: {str(e)}")
                logger.error(f"Raw package content: {encrypted_package[:200]}...")  # Show first 200 chars
                raise

            # Validate required fields exist
            required_fields = ['encrypted_data', 'falcon_signature', 'metadata']
            missing_fields = [field for field in required_fields if field not in package]
            if missing_fields:
                logger.error(f"Missing required fields in package: {missing_fields}")
                raise ValueError(f"Package missing required fields: {missing_fields}")

            # Now we can safely access the fields
            metadata = package.get('metadata', {})
            logger.debug(f"Metadata contents: {metadata}")

            try:
                encrypted_data = base64.b64decode(package['encrypted_data'])
                logger.debug(f"Successfully decoded encrypted_data, length: {len(encrypted_data)}")
            except Exception as e:
                logger.error(f"Failed to decode encrypted_data: {str(e)}")
                raise

            try:
                signature = base64.b64decode(package['falcon_signature'])
                logger.debug(f"Successfully decoded signature, length: {len(signature)}")
            except Exception as e:
                logger.error(f"Failed to decode falcon_signature: {str(e)}")
                raise

            # Extract metadata components with validation
            try:
                if 'quantum_key' not in metadata:
                    logger.error("quantum_key missing from metadata")
                    logger.debug(f"Available metadata keys: {list(metadata.keys())}")
                    raise KeyError("quantum_key not found in metadata")

                quantum_key = int(metadata['quantum_key'])
                logger.debug(f"Successfully extracted quantum_key: {quantum_key}")
            except ValueError as e:
                logger.error(f"Failed to convert quantum_key to int: {str(e)}")
                logger.debug(f"Raw quantum_key value: {metadata.get('quantum_key')}")
                raise

            try:
                if 'entropy' not in metadata:
                    logger.error("entropy missing from metadata")
                    raise KeyError("entropy not found in metadata")

                stored_entropy = json.loads(base64.b64decode(metadata['entropy']).decode('utf-8'))
                logger.debug(f"Successfully decoded entropy data")
            except Exception as e:
                logger.error(f"Failed to process entropy data: {str(e)}")
                raise

            # Verify signature
            logger.debug("Starting signature verification...")
            logger.debug(f"Public key available: {bool(self.public_key)}")
            logger.debug(f"Public key length: {len(self.public_key)}")
            verification_result = self.verify_message(encrypted_data, signature, self.public_key)
            logger.debug(f"Signature verification result: {verification_result}")

            if not verification_result:
                logger.error("Signature verification failed with components:")
                logger.error(f"- Encrypted data length: {len(encrypted_data)}")
                logger.error(f"- Signature length: {len(signature)}")
                logger.error(f"- Public key length: {len(self.public_key)}")
                raise ValueError("Invalid Falcon signature")

            # Reconstruct encryption key with debug info
            encryption_components = [
                str(self.user_key).encode(),
                str(quantum_key).encode(),
                json.dumps(stored_entropy, sort_keys=True).encode()
            ]
            logger.debug("Reconstructing encryption key components:")
            logger.debug(f" - User key: {self.user_key}")
            logger.debug(f" - Quantum key: {quantum_key}")
            logger.debug(f" - Entropy: {json.dumps(stored_entropy, sort_keys=True)}")

            encryption_key = hashlib.sha256(b''.join(encryption_components)).digest()
            logger.debug(f"Reconstructed encryption key length: {len(encryption_key)}")

            # Decrypt data with debug info
            cipher = Fernet(base64.urlsafe_b64encode(encryption_key))
            decrypted_data = cipher.decrypt(encrypted_data)
            logger.debug(f"Decrypted data length: {len(decrypted_data)}")

            # Try to parse JSON with debugging
            try:
                result = json.loads(decrypted_data.decode('utf-8'))
                logger.debug("Successfully parsed decrypted data as JSON")
                return result
            except json.JSONDecodeError:
                logger.debug("Decrypted data is not JSON, returning as bytes")
                return decrypted_data

        except Exception as e:
            logger.error(f"Decryption failed: {str(e)}")
            logger.exception("Decryption stack trace:")
            raise Exception(f"Decryption failed: {e}")

    def decrypt_phi(self, encrypted_package: bytes) -> Dict[str, Any]:
        try:
            logger.debug("Starting PHI decryption")
            # Use the general decrypt method
            decrypted_data = self.decrypt(encrypted_package)
            logger.debug("Successfully decrypted data, validating PHI format")

            # Ensure decrypted data is a dictionary
            if isinstance(decrypted_data, bytes):
                try:
                    phi_data = json.loads(decrypted_data.decode('utf-8'))
                    logger.debug("Successfully parsed bytes to JSON")
                except json.JSONDecodeError:
                    logger.error("Failed to parse decrypted data as JSON")
                    raise ValueError("PHI data must be in JSON format")
            else:
                phi_data = decrypted_data

            # Validate PHI data structure
            required_phi_fields = ["patient_id", "medical_data"]
            if not all(field in phi_data for field in required_phi_fields):
                missing_fields = [field for field in required_phi_fields if field not in phi_data]
                logger.error(f"Missing required PHI fields: {missing_fields}")
                raise ValueError("Invalid or incomplete PHI data")

            logger.debug("Successfully validated PHI data structure")
            return phi_data

        except Exception as e:
            logger.error(f"PHI Decryption failed: {str(e)}")
            raise Exception(f"PHI Decryption failed: {str(e)}")


class HyperledgerHealthInterface:
    def __init__(self, org_id: str):
        self.org_id = org_id
        self.user_key = None
        self.active_sessions = {}

        try:
            # Generate both Falcon and Kyber keys
            logger.debug("Generating keypairs")
            self.keys = SecureKeyManager.generate_all_keys(org_id)

            # Log key details
            logger.debug(f"Falcon public key length: {len(self.keys['falcon']['public_key'])}")
            logger.debug(f"Falcon private key length: {len(self.keys['falcon']['private_key'])}")
            logger.debug(f"Kyber public key length: {len(self.keys['kyber']['public_key'])}")
            logger.debug(f"Kyber private key length: {len(self.keys['kyber']['private_key'])}")

            # Convert Falcon keys to ubyte pointers for compatibility
            self.falcon_public_key = convert_to_ubyte_pointer(self.keys['falcon']['public_key'])
            self.falcon_private_key = convert_to_ubyte_pointer(self.keys['falcon']['private_key'])

            # Store Kyber keys directly (no pointer conversion needed)
            self.kyber_public_key = self.keys['kyber']['public_key']
            self.kyber_private_key = self.keys['kyber']['private_key']

            logger.info(f"Generated all keypairs for {org_id}")

            # Initialize PostQuantumSessionSecurity with both key pairs
            self.post_quantum = PostQuantumSessionSecurity(
                falcon_public_key=bytes(self.falcon_public_key[:FALCON_PUBLIC_KEY_SIZE]),
                falcon_private_key=bytes(self.falcon_private_key[:FALCON_PRIVATE_KEY_SIZE]),
                kyber_public_key=self.kyber_public_key,
                kyber_private_key=self.kyber_private_key
            )

            # Initialize security system
            self.security = HybridSecuritySystem(
                org_id=org_id,
                user_key=0,  # Default value, will be updated with set_user_key
                post_quantum=self.post_quantum,
                public_key=bytes(self.falcon_public_key[:FALCON_PUBLIC_KEY_SIZE]),
                private_key=bytes(self.falcon_private_key[:FALCON_PRIVATE_KEY_SIZE])
            )

        except Exception as e:
            logger.error(f"Failed to initialize {org_id} interface: {str(e)}")
            raise

    def set_user_key(self, key: int):
        """Update the user key and reinitialize security system"""
        try:
            self.user_key = key
            self.security = HybridSecuritySystem(
                org_id=self.org_id,
                user_key=key,
                post_quantum=self.post_quantum,
                public_key=bytes(self.falcon_public_key[:FALCON_PUBLIC_KEY_SIZE]),
                private_key=bytes(self.falcon_private_key[:FALCON_PRIVATE_KEY_SIZE])
            )
            logger.info(f"Updated security system for {self.org_id} with new user key")
        except Exception as e:
            logger.error(f"Failed to update user key: {str(e)}")
            raise

    def rotate_keys(self):
        """Rotate both Falcon and Kyber keys"""
        try:
            logger.debug("Starting key rotation")

            # Generate new keys
            new_keys = SecureKeyManager.generate_all_keys(self.org_id)

            # Update Falcon keys
            self.falcon_public_key = convert_to_ubyte_pointer(new_keys['falcon']['public_key'])
            self.falcon_private_key = convert_to_ubyte_pointer(new_keys['falcon']['private_key'])

            # Update Kyber keys
            self.kyber_public_key = new_keys['kyber']['public_key']
            self.kyber_private_key = new_keys['kyber']['private_key']

            # Reinitialize PostQuantumSessionSecurity
            self.post_quantum = PostQuantumSessionSecurity(
                falcon_public_key=bytes(self.falcon_public_key[:FALCON_PUBLIC_KEY_SIZE]),
                falcon_private_key=bytes(self.falcon_private_key[:FALCON_PRIVATE_KEY_SIZE]),
                kyber_public_key=self.kyber_public_key,
                kyber_private_key=self.kyber_private_key
            )

            # Reinitialize security system
            self.security = HybridSecuritySystem(
                org_id=self.org_id,
                user_key=self.user_key or 0,
                post_quantum=self.post_quantum,
                public_key=bytes(self.falcon_public_key[:FALCON_PUBLIC_KEY_SIZE]),
                private_key=bytes(self.falcon_private_key[:FALCON_PRIVATE_KEY_SIZE])
            )

            logger.info("Successfully rotated all keys")
            return new_keys

        except Exception as e:
            logger.error(f"Failed to rotate keys: {str(e)}")
            raise

    def send_medical_message(self, sender_id: str, recipient_id: str,
                             message: Union[bytes, Dict[str, Any]], session_id: str) -> Dict[str, Any]:
        """Send a medical message using quantum-enhanced security"""
        try:
            # Verify session
            if not self.verify_session(session_id, sender_id):
                raise ValueError(f"Invalid session for user {sender_id}")

            logger.info(f"Session {session_id} verified for user {sender_id}")

            # Handle message encoding
            if isinstance(message, dict):
                message_bytes = json.dumps(message).encode('utf-8')
            elif isinstance(message, bytes):
                message_bytes = message
            else:
                raise ValueError(f"Unsupported message type: {type(message)}")

            # Encrypt message using hybrid security
            encrypted_message = self.security.encrypt_message(message_bytes)

            # Create transaction payload
            transaction_payload = {
                'sender': sender_id,
                'recipient': recipient_id,
                'message': base64.b64encode(encrypted_message).decode('utf-8'),
                'session_id': session_id,
                'timestamp': datetime.now().isoformat(),
                'org_id': self.org_id,
                'message_id': str(uuid.uuid4())
            }

            return transaction_payload

        except Exception as e:
            logger.error(f"Failed to send medical message: {str(e)}")
            raise

    def _generate_session_key(self) -> Tuple[bytes, Dict[str, Any]]:
        """Generate a secure session key using quantum and post-quantum components"""
        try:
            # Generate quantum components
            quantum_key = self.post_quantum.quantum_gen.generate_quantum_key()
            entropy_data = self.post_quantum.entropy.get_astronomical_data()

            # Generate Kyber encryption
            shared_secret, ciphertext = kyber_encap(self.kyber_public_key)

            # Create session key combining all entropy sources
            session_key = hashlib.sha256(
                b''.join([
                    shared_secret,
                    str(quantum_key).encode(),
                    json.dumps(entropy_data, sort_keys=True).encode()
                ])
            ).digest()

            # Package session security data
            security_data = {
                'quantum_key': str(quantum_key),
                'entropy_data': entropy_data,
                'kyber_ciphertext': base64.b64encode(ciphertext).decode('utf-8'),
                'key_hash': hashlib.sha256(session_key).hexdigest()
            }

            return session_key, security_data

        except Exception as e:
            logger.error(f"Failed to generate session key: {str(e)}")
            raise

    def create_session(self, user_id: str, role: str) -> str:
        """Create a new secure session"""
        try:
            # Log session creation attempt
            logger.debug(f"Creating session for user: {user_id}, role: {role}")
            logger.debug("Current active sessions: {len(self.active_sessions)}")

            # Generate session key and security data
            session_key, security_data = self._generate_session_key()

            # Generate session ID using session key
            session_id = hashlib.sha256(
                f"{user_id}{role}{datetime.now().isoformat()}{security_data['quantum_key']}".encode('utf-8')
            ).hexdigest()

            # Create session data with enhanced security
            session_data = {
                'user_id': user_id,
                'role': role,
                'created_at': datetime.now().isoformat(),
                'org_id': self.org_id,
                'expiry': (datetime.now() + timedelta(hours=1)).isoformat(),
                **security_data,  # Include all security data
                'session_hash': hashlib.sha256(
                    f"{session_id}{session_key.hex()}{self.org_id}".encode()
                ).hexdigest()
            }

            # Sign session data with Falcon
            message = json.dumps(session_data).encode('utf-8')
            signature = falcon_sign_message(
                message,
                bytes(self.falcon_private_key[:FALCON_PRIVATE_KEY_SIZE])
            )
            session_data['signature'] = base64.b64encode(signature).decode('utf-8')

            # Store session
            self.active_sessions[session_id] = session_data

            logger.info(f"Session created successfully: {session_id}")
            logger.debug(f"Active sessions count: {len(self.active_sessions)}")
            return session_id

        except Exception as e:
            logger.error(f"Session creation failed: {str(e)}")
            logger.error(f"Active sessions at error: {self.active_sessions}")
            raise

    def verify_session(self, session_id: str, user_id: str) -> bool:
        """Verify session validity and integrity"""
        try:
            logger.debug(f"Verifying session - ID: {session_id}, User: {user_id}")
            logger.debug(f"Total active sessions: {len(self.active_sessions)}")

            # Check if session exists
            if session_id not in self.active_sessions:
                logger.warning("Session not found in active sessions")
                return False

            session = self.active_sessions[session_id]

            # Verify session integrity
            if 'session_hash' not in session or 'signature' not in session:
                logger.error("Session missing security components")
                return False

            # Verify signature
            try:
                message = json.dumps({k: v for k, v in session.items()
                                      if k not in ['signature']}).encode('utf-8')
                signature = base64.b64decode(session['signature'])

                if not falcon_verify_signature(
                        message,
                        signature,
                        bytes(self.falcon_public_key[:FALCON_PUBLIC_KEY_SIZE])
                ):
                    logger.error("Session signature verification failed")
                    return False
            except Exception as e:
                logger.error(f"Signature verification error: {str(e)}")
                return False

            # Check expiration
            current_time = datetime.now()
            session_expiry = datetime.fromisoformat(session['expiry'])

            if current_time > session_expiry:
                logger.warning("Session has expired")
                del self.active_sessions[session_id]
                return False

            # Verify user match
            if session['user_id'] != user_id:
                logger.warning("User ID mismatch")
                return False

            # Verify session hash
            expected_hash = hashlib.sha256(
                f"{session_id}{session['quantum_key']}{self.org_id}".encode()
            ).hexdigest()
            if session['session_hash'] != expected_hash:
                logger.error("Session hash verification failed")
                return False

            logger.debug("Session verified successfully")
            return True

        except Exception as e:
            logger.error(f"Session verification error: {str(e)}")
            return False

    def extend_session(self, session_id: str, duration: int = 3600) -> bool:
        """Extend session duration with new security parameters"""
        try:
            if session_id not in self.active_sessions:
                return False

            session = self.active_sessions[session_id]

            # Generate new session key and security data
            new_session_key, new_security_data = self._generate_session_key()

            # Update session
            session.update({
                'expiry': (datetime.now() + timedelta(seconds=duration)).isoformat(),
                **new_security_data,  # Include all new security data
                'extended_at': datetime.now().isoformat(),
                'session_hash': hashlib.sha256(
                    f"{session_id}{new_session_key.hex()}{self.org_id}".encode()
                ).hexdigest()
            })

            # Update signature
            message = json.dumps({k: v for k, v in session.items()
                                  if k not in ['signature']}).encode('utf-8')
            signature = falcon_sign_message(
                message,
                bytes(self.falcon_private_key[:FALCON_PRIVATE_KEY_SIZE])
            )
            session['signature'] = base64.b64encode(signature).decode('utf-8')

            logger.info(f"Successfully extended session: {session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to extend session: {str(e)}")
            return False

    def revoke_session(self, session_id: str) -> bool:
        """Revoke a session"""
        try:
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
                logger.info(f"Successfully revoked session: {session_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to revoke session: {str(e)}")
            return False

    def receive_medical_message(self, recipient_id: str, tx_id: str, session_id: str) -> Dict[str, Any]:
        """Receive and decrypt a medical message"""
        try:
            # Verify session
            if not self.verify_session(session_id, recipient_id):
                raise ValueError(f"Invalid session for user {recipient_id}")

            # Placeholder for chaincode query
            fabric_record = {
                'recipient': recipient_id,
                'message': '',  # Base64 encoded encrypted message
                'timestamp': datetime.now().isoformat()
            }

            # Verify recipient
            if fabric_record['recipient'] != recipient_id:
                raise ValueError(f"Message not intended for recipient {recipient_id}")

            try:
                encrypted_message = base64.b64decode(fabric_record['message'])
                decrypted_message = self.security.decrypt_message(encrypted_message)

                if not isinstance(decrypted_message, dict):
                    raise ValueError("Decrypted message is not in correct format")

                decrypted_message['received_at'] = datetime.now().isoformat()
                return decrypted_message

            except Exception as e:
                logger.error(f"Failed to decrypt message: {str(e)}")
                return {
                    'error': str(e),
                    'recipient_id': recipient_id,
                    'tx_id': tx_id,
                    'timestamp': datetime.now().isoformat()
                }

        except Exception as e:
            logger.error(f"Failed to receive medical message: {str(e)}")
            raise







def _verify_session(self, session_id: str, required_action: str, user_id: str) -> bool:
    """
    Verifies if a session is valid and has the required permissions

    Args:
        session_id: The session ID to verify
        required_action: The action being requested
        user_id: The ID of the user making the request

    Returns:
        bool: True if session is valid and has required permissions
    """
    if session_id not in self.access.active_sessions:
        return False
    session = self.access.active_sessions[session_id]
    return (session['user_id'] == user_id and
            self.access.verify_access(user_id, session['role'], required_action))
