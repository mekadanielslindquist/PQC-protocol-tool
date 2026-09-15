import os
from ctypes import POINTER, RTLD_GLOBAL
from ctypes import cdll, create_string_buffer, c_uint8, c_size_t, byref, c_int, CDLL
import logging

# =============================
# Logging Setup
# =============================

logger = logging.getLogger("FalconLogger")
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

# =============================
# Falcon Constants
# =============================
FALCON_PRIVATE_KEY_SIZE = 2305  # Size of Falcon-1024 private key
FALCON_PUBLIC_KEY_SIZE = 1793  # Size of Falcon-1024 public key
FALCON_SIGNATURE_SIZE = 1330  # Size of Falcon-1024 signature

# =============================
# Load Falcon Dynamic Library
# =============================

def load_falcon_library():
    lib_path = os.path.join(os.path.dirname(__file__),
                            "PQClean/crypto_sign/falcon-1024/aarch64/libfalcon-1024_aarch64.so")

    print(f"Current directory: {os.path.dirname(__file__)}")
    print(f"Looking for library at: {lib_path}")
    print(f"File exists: {os.path.exists(lib_path)}")

    if not os.path.exists(lib_path):
        logging.error(f"Library not found: {lib_path}")
        raise FileNotFoundError(f"Library not found: {lib_path}")

    try:
        lib = CDLL(lib_path, mode=RTLD_GLOBAL)
        print(f"Successfully loaded library: {lib}")
        return lib
    except Exception as e:
        print(f"Detailed error loading library: {str(e)}")
        print(f"Library path: {lib_path}")
        print(f"Directory contents: {os.listdir(os.path.dirname(lib_path))}")
        raise RuntimeError(f"Could not load Falcon library: {str(e)}")


falcon_lib = load_falcon_library()

if hasattr(falcon_lib, 'PQCLEAN_FALCON1024_CLEAN_crypto_sign_keypair'):
    print("Found keypair function")
else:
    print("Missing keypair function")

# Define function signatures for Falcon library
falcon_lib.PQCLEAN_FALCON1024_CLEAN_crypto_sign_keypair.argtypes = [
    POINTER(c_uint8), POINTER(c_uint8)
]
falcon_lib.PQCLEAN_FALCON1024_CLEAN_crypto_sign_keypair.restype = c_int

falcon_lib.PQCLEAN_FALCON1024_CLEAN_crypto_sign_signature.argtypes = [
    POINTER(c_uint8), POINTER(c_size_t),
    POINTER(c_uint8), c_size_t,
    POINTER(c_uint8)
]
falcon_lib.PQCLEAN_FALCON1024_CLEAN_crypto_sign_signature.restype = c_int

falcon_lib.PQCLEAN_FALCON1024_CLEAN_crypto_sign_verify.argtypes = [
    POINTER(c_uint8), c_size_t,
    POINTER(c_uint8), c_size_t,
    POINTER(c_uint8)
]
falcon_lib.PQCLEAN_FALCON1024_CLEAN_crypto_sign_verify.restype = c_int


# =============================
# Falcon Wrapper Functions
# =============================

def falcon_generate_keypair():
    """
    Generate a Falcon keypair (public and private key).
    """
    try:
        # Allocate buffers
        public_key = (c_uint8 * FALCON_PUBLIC_KEY_SIZE)()
        private_key = (c_uint8 * FALCON_PRIVATE_KEY_SIZE)()

        # Call the keypair function
        result = falcon_lib.PQCLEAN_FALCON1024_CLEAN_crypto_sign_keypair(
            public_key, private_key
        )

        if result != 0:
            raise RuntimeError(f"Falcon keypair generation failed with error code: {result}")

        print("Falcon keypair generated successfully.")
        return bytes(public_key), bytes(private_key)

    except Exception as e:
        print(f"Error in keypair generation: {e}")
        raise


def falcon_sign_message(message: bytes, private_key: bytes):
    """
    Sign a message using Falcon's private key.
    """
    if len(private_key) != FALCON_PRIVATE_KEY_SIZE:
        raise ValueError("Invalid private key size.")

    signature = (c_uint8 * FALCON_SIGNATURE_SIZE)()
    sig_len = c_size_t(FALCON_SIGNATURE_SIZE)

    result = falcon_lib.PQCLEAN_FALCON1024_CLEAN_crypto_sign_signature(
        signature, byref(sig_len),
        (c_uint8 * len(message)).from_buffer_copy(message),
        len(message),
        (c_uint8 * len(private_key)).from_buffer_copy(private_key)
    )

    if result != 0:
        raise RuntimeError(f"Falcon signing failed with error code: {result}")

    print(f"Falcon signature generated successfully. Signature size: {sig_len.value} bytes")
    return bytes(signature[:sig_len.value])


def falcon_verify_signature(message: bytes, signature: bytes, public_key: bytes) -> bool:
    """
    Verify a Falcon signature using the public key.
    """
    if len(public_key) != FALCON_PUBLIC_KEY_SIZE:
        raise ValueError("Invalid public key size.")

    result = falcon_lib.PQCLEAN_FALCON1024_CLEAN_crypto_sign_verify(
        (c_uint8 * len(signature)).from_buffer_copy(signature),
        len(signature),
        (c_uint8 * len(message)).from_buffer_copy(message),
        len(message),
        (c_uint8 * len(public_key)).from_buffer_copy(public_key)
    )

    if result == 0:
        print("Falcon signature verification succeeded.")
        return True
    else:
        print("Falcon signature verification failed.")
        return False


# =============================
# Main Execution for Testing
# =============================

if __name__ == "__main__":
    try:
        # Generate a keypair
        public_key, private_key = falcon_generate_keypair()

        # Test message
        test_message = b"Test Message for Falcon Signing"

        # Sign the message
        signature = falcon_sign_message(test_message, private_key)

        # Verify the signature
        is_valid = falcon_verify_signature(test_message, signature, public_key)
        logger.info(f"Signature verification result: {is_valid}")

    except Exception as e:
        logger.error(f"An error occurred during Falcon operations: {e}")




