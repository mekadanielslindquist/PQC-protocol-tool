#!/bin/bash
# fabric_commands.sh
# Essential commands for managing Hyperledger Fabric network with quantum security

# Set environment variables
export FABRIC_CFG_PATH=${PWD}/config
export CORE_PEER_TLS_ENABLED=true
export CORE_PEER_LOCALMSPID=HospitalAMSP
export CORE_PEER_TLS_ROOTCERT_FILE=${PWD}/crypto-config/peerOrganizations/Hospital_A.example.com/peers/peer0.Hospital_A.example.com/tls/ca.crt
export CORE_PEER_MSPCONFIGPATH=${PWD}/crypto-config/peerOrganizations/Hospital_A.example.com/users/Admin@Hospital_A.example.com/msp
export CORE_PEER_ADDRESS=localhost:7051
export ORDERER_CA=${PWD}/crypto-config/ordererOrganizations/example.com/orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem

# 1. Generate genesis block and channel transaction
generate_genesis() {
    echo "Generating genesis block and channel transaction..."

    # Create configtx.yaml if it doesn't exist
    if [ ! -f "${FABRIC_CFG_PATH}/configtx.yaml" ]; then
        mkdir -p ${FABRIC_CFG_PATH}
        cp network_config.yaml ${FABRIC_CFG_PATH}/configtx.yaml
        echo "Created configtx.yaml from network_config.yaml"
    fi

    # Generate genesis block
    configtxgen -profile TwoOrgsOrdererGenesis -channelID system-channel -outputBlock ./channel-artifacts/genesis.block

    # Generate channel transaction
    configtxgen -profile TwoOrgsChannel -outputCreateChannelTx ./channel-artifacts/channel.tx -channelID mychannel

    # Generate anchor peer updates
    configtxgen -profile TwoOrgsChannel -outputAnchorPeersUpdate ./channel-artifacts/HospitalAMSPanchors.tx -channelID mychannel -asOrg HospitalA
    configtxgen -profile TwoOrgsChannel -outputAnchorPeersUpdate ./channel-artifacts/HospitalBMSPanchors.tx -channelID mychannel -asOrg HospitalB

    echo "Genesis block and channel transaction generated"
}

# 2. Start the network with quantum security
start_network() {
    echo "Starting the network with quantum security..."

    # Ensure quantum keys are generated
    echo "Verifying quantum keys..."
    if [ ! -f "crypto-config/peerOrganizations/Hospital_A.example.com/peers/peer0.Hospital_A.example.com/quantum_keys/Hospital_A_falcon_private.key" ]; then
        echo "Quantum keys missing. Running quantum_cryptogen.py..."
        python3 quantum_cryptogen.py generate --config=crypto-config.yaml
    fi

    # Start with docker-compose
    docker compose up -d

    echo "Network started. Waiting for containers to stabilize..."
    sleep 10

    echo "Network status:"
    docker ps -a
}

# 3. Create and join channel
create_channel() {
    echo "Creating and joining channel..."

    # Create channel using peer CLI
    docker exec cli peer channel create -o orderer.example.com:7050 -c mychannel \
        -f /opt/gopath/src/github.com/hyperledger/fabric/peer/channel-artifacts/channel.tx \
        --tls --cafile $ORDERER_CA

    # Join peer to channel
    docker exec cli peer channel join -b mychannel.block

    # Update anchor peers
    docker exec cli peer channel update -o orderer.example.com:7050 -c mychannel \
        -f /opt/gopath/src/github.com/hyperledger/fabric/peer/channel-artifacts/HospitalAMSPanchors.tx \
        --tls --cafile $ORDERER_CA

    echo "Channel created and joined"
}

# 4. Install and instantiate chaincode
install_chaincode() {
    echo "Installing and instantiating chaincode..."

    # Package the chaincode
    docker exec cli peer lifecycle chaincode package quantum_records.tar.gz \
        --path /opt/gopath/src/github.com/hyperledger/fabric/peer/chaincode/quantum_records \
        --lang golang --label quantum_records_1.0

    # Install the chaincode
    docker exec cli peer lifecycle chaincode install quantum_records.tar.gz

    # Approve the chaincode for org1
    docker exec cli peer lifecycle chaincode approveformyorg --channelID mychannel \
        --name quantum_records --version 1.0 --package-id quantum_records_1.0:$(docker exec cli bash -c "peer lifecycle chaincode calculatepackageid quantum_records.tar.gz") \
        --sequence 1 --tls --cafile $ORDERER_CA

    # Switch to org2 context to approve chaincode
    docker exec -e CORE_PEER_LOCALMSPID=HospitalBMSP \
        -e CORE_PEER_TLS_ROOTCERT_FILE=/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/Hospital_B.example.com/peers/peer0.Hospital_B.example.com/tls/ca.crt \
        -e CORE_PEER_MSPCONFIGPATH=/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/Hospital_B.example.com/users/Admin@Hospital_B.example.com/msp \
        -e CORE_PEER_ADDRESS=peer0.Hospital_B.example.com:7061 \
        cli peer lifecycle chaincode approveformyorg --channelID mychannel \
        --name quantum_records --version 1.0 --package-id quantum_records_1.0:$(docker exec cli bash -c "peer lifecycle chaincode calculatepackageid quantum_records.tar.gz") \
        --sequence 1 --tls --cafile $ORDERER_CA

    # Commit the chaincode definition
    docker exec cli peer lifecycle chaincode commit -o orderer.example.com:7050 \
        --channelID mychannel --name quantum_records --version 1.0 --sequence 1 \
        --tls --cafile $ORDERER_CA \
        --peerAddresses peer0.Hospital_A.example.com:7051 \
        --tlsRootCertFiles /opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/Hospital_A.example.com/peers/peer0.Hospital_A.example.com/tls/ca.crt \
        --peerAddresses peer0.Hospital_B.example.com:7061 \
        --tlsRootCertFiles /opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/Hospital_B.example.com/peers/peer0.Hospital_B.example.com/tls/ca.crt

    echo "Chaincode installed and instantiated"
}

# 5. Test chaincode
test_chaincode() {
    echo "Testing chaincode..."

    # Initialize the ledger
    docker exec cli peer chaincode invoke -o orderer.example.com:7050 \
        -C mychannel -n quantum_records \
        --tls --cafile $ORDERER_CA \
        -c '{"function":"initLedger","Args":[]}'

    sleep 5

    # Query the ledger
    docker exec cli peer chaincode query -C mychannel -n quantum_records -c '{"Args":["queryAllRecords"]}'

    echo "Chaincode tested successfully"
}

# 6. Test Hedera integration
test_hedera_bridge() {
    echo "Testing Hedera bridge..."

    # Submit a transaction to Hedera via the bridge
    docker exec hedera-bridge python3 -c "
from hedera_bridge import HederaFabricBridge
bridge = HederaFabricBridge('Hospital_A')
health = bridge.check_health()
print(f'Hedera Bridge Health: {health}')

if health['status'] == 'healthy':
    data = {
        'patientId': 'anonymized_12345',
        'recordType': 'test',
        'timestamp': 1234567890,
        'operation': 'test',
        'metadata': {
            'department': 'test',
            'authorized': True,
            'quantumSecured': True
        }
    }
    tx_id = bridge.submit_to_hedera(data)
    print(f'Transaction submitted to Hedera: {tx_id}')

    reference_data = {
        'dataType': 'test_record',
        'timestamp': 1234567890,
        'department': 'test',
        'demo': True
    }

    success = bridge.store_transaction_reference(tx_id, reference_data)
    print(f'Reference stored in Fabric: {success}')

    result = bridge.query_transaction(tx_id)
    print(f'Transaction found in: {result.get(\"foundIn\", [])}')
"

    echo "Hedera bridge tested"
}

# 7. Stop the network
stop_network() {
    echo "Stopping the network..."
    docker compose down
    echo "Network stopped"
}

# Main execution
case "$1" in
    "generate")
        generate_genesis
        ;;
    "start")
        start_network
        ;;
    "create-channel")
        create_channel
        ;;
    "install-chaincode")
        install_chaincode
        ;;
    "test-chaincode")
        test_chaincode
        ;;
    "test-hedera")
        test_hedera_bridge
        ;;
    "stop")
        stop_network
        ;;
    "all")
        generate_genesis
        start_network
        create_channel
        install_chaincode
        test_chaincode
        test_hedera_bridge
        ;;
    *)
        echo "Usage: $0 {generate|start|create-channel|install-chaincode|test-chaincode|test-hedera|stop|all}"
        echo ""
        echo "Commands:"
        echo "  generate         - Generate genesis block and channel artifacts"
        echo "  start            - Start the network with quantum security"
        echo "  create-channel   - Create and join the channel"
        echo "  install-chaincode - Install and instantiate the chaincode"
        echo "  test-chaincode   - Test the chaincode with sample invocations"
        echo "  test-hedera      - Test the Hedera bridge integration"
        echo "  stop             - Stop the network"
        echo "  all              - Execute all commands in sequence"
        exit 1
        ;;
esac

exit 0