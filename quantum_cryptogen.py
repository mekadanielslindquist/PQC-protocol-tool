import logging
import subprocess
from pathlib import Path
import argparse
import sys
import shutil
import os

# Add parent directory to Python path to find modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
sys.path.append(os.path.join(current_dir, 'sip_connect'))

# Import quantum crypto functions
from sip_connect.key_utils import convert_to_ubyte_pointer
from sip_connect.kyber_wrapper import kyber_keygen
from sip_connect.hipaa_security import SecureKeyManager


def setup_logging():
    """Configure logging"""
    logging_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=logging.INFO,  # Change to INFO or DEBUG
        format=logging_format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('quantum_crypto.log', mode='w')  # 'w' mode to overwrite each time
        ]
    )
    return logging.getLogger('QuantumCryptoGen')


def save_asterisk_keys(org_id: str, keys: dict, logger):
    """Save quantum keys for Asterisk"""
    try:
        # Define Asterisk paths
        asterisk_base = Path('/etc/asterisk')
        key_path = asterisk_base / 'keys' / 'keys'
        key_path.mkdir(parents=True, exist_ok=True)

        # Save Falcon keys with Asterisk naming
        falcon_public_bytes = bytes(keys['falcon']['public'][:1793])
        falcon_private_bytes = bytes(keys['falcon']['private'][:2305])

        with open(key_path / f'{org_id}_falcon_public.pem', 'wb') as f:
            f.write(falcon_public_bytes)
        logger.info(f"Saved Asterisk Falcon Public Key for {org_id}")

        with open(key_path / f'{org_id}_falcon_private.pem', 'wb') as f:
            f.write(falcon_private_bytes)
        logger.info(f"Saved Asterisk Falcon Private Key for {org_id}")

        # Save Kyber keys with Asterisk naming
        kyber_public_bytes = bytes(keys['kyber']['public'])
        kyber_private_bytes = bytes(keys['kyber']['private'])

        with open(key_path / f'{org_id}_kyber_public.pem', 'wb') as f:
            f.write(kyber_public_bytes)
        logger.info(f"Saved Asterisk Kyber Public Key for {org_id}")

        with open(key_path / f'{org_id}_kyber_private.pem', 'wb') as f:
            f.write(kyber_private_bytes)
        logger.info(f"Saved Asterisk Kyber Private Key for {org_id}")

        # Set proper permissions for Asterisk
        for key_file in key_path.glob('*.pem'):
            key_file.chmod(0o640)
            shutil.chown(str(key_path), user='asterisk', group='asterisk')

        logger.info(f"Quantum keys successfully saved for Asterisk {org_id}")

    except Exception as e:
        logger.error(f"Error saving Asterisk quantum keys for {org_id}: {str(e)}")
        raise


def create_asterisk_config(org_id: str, logger):
    """Create Asterisk quantum configuration"""
    try:
        asterisk_base = Path('/etc/asterisk')
        config_path = asterisk_base / 'quantum.conf'

        config_content = f"""[general]
quantum_enabled=yes
org_id={org_id}
key_store=/etc/asterisk/keys/keys

[quantum_keys]
falcon_public_key={org_id}_falcon_public.pem
falcon_private_key={org_id}_falcon_private.pem
kyber_public_key={org_id}_kyber_public.pem
kyber_private_key={org_id}_kyber_private.pem

[security]
refresh_interval=3600
key_rotation_enabled=yes
"""
        with open(config_path, 'w') as f:
            f.write(config_content)

        logger.info(f"Created Asterisk quantum configuration for {org_id}")

    except Exception as e:
        logger.error(f"Error creating Asterisk configuration: {str(e)}")
        raise


def generate_keys_for_org(org_id: str, logger):
    """Generate both Falcon and Kyber keys for an organization"""
    try:
        logger.info(f"Generating quantum keys for {org_id}")

        # Generate Falcon keys
        falcon_keys = SecureKeyManager.generate_falcon_keypair(org_id)
        falcon_public_ptr = convert_to_ubyte_pointer(falcon_keys['public_key'])
        falcon_private_ptr = convert_to_ubyte_pointer(falcon_keys['private_key'])

        # Generate Kyber keys
        public_key, private_key = kyber_keygen()
        if not public_key or not private_key:
            logger.error(f"Failed to generate Kyber keys for {org_id}")
            raise ValueError("Kyber key generation failed")

        keys = {
            'falcon': {
                'public': falcon_public_ptr,
                'private': falcon_private_ptr
            },
            'kyber': {
                'public': public_key,
                'private': private_key
            }
        }

        # Save keys for Hyperledger (required)
        save_keys(org_id, keys, logger)

        # Asterisk key/config export is only relevant when the Asterisk
        # service itself is being deployed (currently set aside). Don't let
        # a host-permission failure here (e.g. no /etc/asterisk on this
        # machine) abort key generation for the rest of the orgs.
        try:
            save_asterisk_keys(org_id, keys, logger)
            create_asterisk_config(org_id, logger)
        except Exception as e:
            logger.warning(f"Skipping Asterisk key export for {org_id} (Asterisk not set up on this host): {str(e)}")

        return keys

    except Exception as e:
        logger.error(f"Error generating quantum keys: {str(e)}")
        raise


def save_keys(org_id: str, keys: dict, logger):
    """Save the generated keys to the correct Fabric directory structure"""
    try:
        # Define paths
        base_path = Path(
            f'crypto-config/peerOrganizations/{org_id}.example.com/peers/peer0.{org_id}.example.com/quantum_keys')
        base_path.mkdir(parents=True, exist_ok=True)

        # Save Falcon keys
        falcon_public_bytes = bytes(keys['falcon']['public'][:1793])
        falcon_private_bytes = bytes(keys['falcon']['private'][:2305])

        with open(base_path / f'{org_id}_falcon_public.key', 'wb') as f:
            f.write(falcon_public_bytes)
        logger.info(f"Saved Falcon Public Key for {org_id}")

        with open(base_path / f'{org_id}_falcon_private.key', 'wb') as f:
            f.write(falcon_private_bytes)
        logger.info(f"Saved Falcon Private Key for {org_id}")

        # Save Kyber keys
        kyber_public_bytes = bytes(keys['kyber']['public'])
        kyber_private_bytes = bytes(keys['kyber']['private'])

        with open(base_path / f'{org_id}_kyber_public.key', 'wb') as f:
            f.write(kyber_public_bytes)
        logger.info(f"Saved Kyber Public Key for {org_id}")

        with open(base_path / f'{org_id}_kyber_private.key', 'wb') as f:
            f.write(kyber_private_bytes)
        logger.info(f"Saved Kyber Private Key for {org_id}")

        logger.info(f"Quantum keys successfully saved for {org_id}")

    except Exception as e:
        logger.error(f"Error saving quantum keys for {org_id}: {str(e)}")
        raise


def verify_msp_structure(logger):
    """Verify the MSP directory structure is correct"""
    try:
        # Verify peer organizations
        orgs = ["Hospital_A", "Hospital_B"]
        for org in orgs:
            base_path = Path(f'crypto-config/peerOrganizations/{org}.example.com/peers/peer0.{org}.example.com')
            required_dirs = ['msp/cacerts', 'msp/keystore', 'msp/signcerts', 'quantum_keys']

            for dir_path in required_dirs:
                full_path = base_path / dir_path
                if not full_path.exists():
                    raise FileNotFoundError(f"Required directory {dir_path} not found for {org}")

        logger.info("MSP directory structure verified")
        return True
    except Exception as e:
        logger.error(f"MSP structure verification failed: {str(e)}")
        raise


def generate_orderer_keys(logger):
    """Generate both Falcon and Kyber keys for orderer"""
    try:
        logger.info("Generating quantum keys for orderer")

        # Generate Falcon keys
        falcon_keys = SecureKeyManager.generate_falcon_keypair("orderer")
        falcon_public_ptr = convert_to_ubyte_pointer(falcon_keys['public_key'])
        falcon_private_ptr = convert_to_ubyte_pointer(falcon_keys['private_key'])

        # Generate Kyber keys
        public_key, private_key = kyber_keygen()
        if not public_key or not private_key:
            logger.error("Failed to generate Kyber keys for orderer")
            raise ValueError("Orderer Kyber key generation failed")

        return {
            'falcon': {
                'public': falcon_public_ptr,
                'private': falcon_private_ptr
            },
            'kyber': {
                'public': public_key,
                'private': private_key
            }
        }
    except Exception as e:
        logger.error(f"Error generating orderer quantum keys: {str(e)}")
        raise


def save_orderer_keys(keys: dict, logger):
    """Save orderer keys"""
    try:
        base_path = Path('crypto-config/ordererOrganizations/example.com/orderers/orderer.example.com/quantum_keys')
        base_path.mkdir(parents=True, exist_ok=True)

        # Save Falcon keys
        falcon_public_bytes = bytes(keys['falcon']['public'][:1793])
        falcon_private_bytes = bytes(keys['falcon']['private'][:2305])

        with open(base_path / 'orderer_falcon_public.key', 'wb') as f:
            f.write(falcon_public_bytes)
        with open(base_path / 'orderer_falcon_private.key', 'wb') as f:
            f.write(falcon_private_bytes)

        # Save Kyber keys
        kyber_public_bytes = bytes(keys['kyber']['public'])
        kyber_private_bytes = bytes(keys['kyber']['private'])

        with open(base_path / 'orderer_kyber_public.key', 'wb') as f:
            f.write(kyber_public_bytes)
        with open(base_path / 'orderer_kyber_private.key', 'wb') as f:
            f.write(kyber_private_bytes)

        logger.info("Successfully saved orderer quantum keys")
    except Exception as e:
        logger.error(f"Error saving orderer quantum keys: {str(e)}")
        raise


def parse_args():
    parser = argparse.ArgumentParser(description='Unified Quantum Cryptogen Tool')
    parser.add_argument('command', type=str, choices=['generate'], help='Command to execute')
    parser.add_argument('--config', type=str, required=True, help='Path to config file')

    # Add debug print to help diagnose
    print("sys.argv:", sys.argv)

    try:
        args = parser.parse_args()
        print("Parsed args:", args)
        return args
    except SystemExit as e:
        print("Argument parsing error:", e)
        raise


def main():
    logger = setup_logging()
    logger.info("Starting quantum key generation process")

    args = parse_args()
    logger.info(f"Command: {args.command}, Config: {args.config}")

    try:
        if args.command == 'generate':
            logger.info("Running cryptogen")
            subprocess.run(["cryptogen", "generate", f"--config={args.config}"], check=True)

            logger.info("Generating orderer keys")
            orderer_keys = generate_orderer_keys(logger)
            save_orderer_keys(orderer_keys, logger)

            orgs = ["Hospital_A", "Hospital_B"]
            for org in orgs:
                logger.info(f"Generating keys for {org}")
                keys = generate_keys_for_org(org, logger)

            logger.info("Verifying MSP structure")
            verify_msp_structure(logger)

            logger.info("Key generation completed successfully")

    except Exception as e:
        logger.error(f"Critical error in key generation: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
