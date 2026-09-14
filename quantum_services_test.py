#!/usr/bin/env python3
import sys
import logging
import paho.mqtt.client as mqtt
import socket
import ssl
import time
from sip_connect.quantum_mqtt import QuantumMQTTClient
from sip_connect.quantum_srtp import QuantumEnhancedSRTP
from sip_connect.hipaa_security import SecureKeyManager, EnhancedEncryption
from sip_connect.sip_encrypt import SIPIntegration

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('QuantumServicesTest')


def test_mqtt_connection(broker='localhost', port=1883):
    """Test basic MQTT connection"""
    logger.info(f"Testing MQTT connection to {broker}:{port}")
    try:
        client = mqtt.Client("test_client")
        client.connect(broker, port, 60)
        client.disconnect()
        logger.info("✅ MQTT Connection successful")
        return True
    except Exception as e:
        logger.error(f"❌ MQTT Connection failed: {e}")
        return False


def test_quantum_mqtt_client(org_id='Hospital_A'):
    """Test Quantum MQTT Client"""
    logger.info(f"Testing Quantum MQTT Client for {org_id}")
    try:
        mqtt_client = QuantumMQTTClient(org_id)
        mqtt_client.connect()

        # Test publish and subscribe
        test_topic = f"{org_id}/test"
        test_message = "Quantum MQTT Test Message"

        # Define callback for testing
        def on_message(client, userdata, message):
            logger.info(f"Received message: {message.payload.decode()}")
            assert message.payload.decode() == test_message, "Message content mismatch"

        mqtt_client.client.on_message = on_message
        mqtt_client.client.subscribe(test_topic)
        mqtt_client.client.publish(test_topic, test_message)

        # Wait for message
        time.sleep(2)

        logger.info("✅ Quantum MQTT Client test successful")
        return True
    except Exception as e:
        logger.error(f"❌ Quantum MQTT Client test failed: {e}")
        return False


def test_sip_connection(host='localhost', port=5060):
    """Test basic SIP connection"""
    logger.info(f"Testing SIP connection to {host}:{port}")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5)

        # Simple OPTIONS request
        sip_options = (
            "OPTIONS sip:test@localhost SIP/2.0\r\n"
            "Via: SIP/2.0/UDP localhost:5060;branch=z9hG4bK776asdhds\r\n"
            "Max-Forwards: 70\r\n"
            "To: <sip:test@localhost>\r\n"
            "From: <sip:tester@localhost>;tag=1928301774\r\n"
            "Call-ID: test_call@localhost\r\n"
            "CSeq: 1 OPTIONS\r\n"
            "Contact: <sip:tester@localhost:5060>\r\n"
            "Accept: application/sdp\r\n"
            "Content-Length: 0\r\n\r\n"
        )

        sock.sendto(sip_options.encode(), (host, port))

        # Try to receive response
        data, _ = sock.recvfrom(1024)
        response = data.decode()

        logger.info(f"SIP Response: {response}")

        # Basic response validation
        if "SIP/2.0 200 OK" in response:
            logger.info("✅ SIP Connection successful")
            return True
        else:
            logger.warning("❌ Unexpected SIP response")
            return False
    except Exception as e:
        logger.error(f"❌ SIP Connection failed: {e}")
        return False


def test_quantum_srtp(org_id='Hospital_A'):
    """Test Quantum Enhanced SRTP"""
    logger.info(f"Testing Quantum SRTP for {org_id}")
    try:
        # Generate keys
        falcon_keys = SecureKeyManager.generate_falcon_keypair(org_id)
        kyber_keys = SecureKeyManager.generate_kyber_keypair(org_id)

        # Prepare keys dictionary
        keys = {
            'falcon': {
                'public_key': falcon_keys['public_key'],
                'private_key': falcon_keys['private_key']
            },
            'kyber': {
                'public_key': kyber_keys['public_key'],
                'private_key': kyber_keys['private_key']
            }
        }

        # Create encryption system
        encryption_system = EnhancedEncryption(
            user_key=int(time.time()),
            org_id=org_id,
            shared_keys=keys
        )

        # Create SIP integration
        sip_connection = SIPIntegration(
            encryption_system,
            host='127.0.0.1',
            port=5061
        )

        # Initialize Quantum Enhanced SRTP
        srtp = QuantumEnhancedSRTP(sip_connection)

        # Setup SRTP session
        session_id = f"{org_id}_test_session_{int(time.time())}"
        session = srtp.setup_srtp_session(session_id)

        logger.info(f"✅ Quantum SRTP session created: {session_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Quantum SRTP test failed: {e}")
        return False


def run_all_tests():
    """Run all quantum service tests"""
    tests = [
        test_mqtt_connection,
        test_quantum_mqtt_client,
        test_sip_connection,
        test_quantum_srtp
    ]

    results = {}
    for test in tests:
        results[test.__name__] = test()

    # Summary
    failed_tests = [name for name, result in results.items() if not result]

    print("\n--- TEST SUMMARY ---")
    for name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{name}: {status}")

    if failed_tests:
        print(f"\n{len(failed_tests)} test(s) failed: {failed_tests}")
        sys.exit(1)
    else:
        print("\nAll tests passed successfully!")
        sys.exit(0)


if __name__ == "__main__":
    run_all_tests()