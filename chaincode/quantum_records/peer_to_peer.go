package main

import (
    "encoding/json"
    "fmt"
    "time"
    "github.com/hyperledger/fabric-contract-api-go/contractapi"
)

// QuantumSecureContract implements the smart contract
type QuantumSecureContract struct {
    contractapi.Contract
}

// Communication represents a secure communication record
type Communication struct {
    ID              string    `json:"id"`
    Type            string    `json:"type"`              // p2p, p2m, m2m
    Sender          string    `json:"sender"`
    Receiver        string    `json:"receiver"`
    QuantumSignature string   `json:"quantumSignature"`
    Timestamp       time.Time `json:"timestamp"`
    MessageHash     string    `json:"messageHash"`
    Protocol        string    `json:"protocol"`          // sip, mqtt
    DeviceVerification string `json:"deviceVerification"` // ARM device verification
    PaymentInfo     *Payment `json:"paymentInfo,omitempty"`
}

// Device represents an ARM device registration
type Device struct {
    ID              string    `json:"id"`
    Type            string    `json:"type"`
    PublicKey       string    `json:"publicKey"`
    QuantumKey      string    `json:"quantumKey"`
    LastVerified    time.Time `json:"lastVerified"`
    Status          string    `json:"status"`
}

// MasterWallet represents the service provider's wallet
type MasterWallet struct {
    ID              string    `json:"id"`
    WalletType      string    `json:"walletType"`      // solana, hedera
    PublicKey       string    `json:"publicKey"`
    Balance         float64   `json:"balance"`
    Status          string    `json:"status"`
    SubWallets      []string  `json:"subWallets"`      // List of sub-wallet IDs
    DailyLimit      float64   `json:"dailyLimit"`
    CreatedAt       time.Time `json:"createdAt"`
}

// SubWallet represents an individual machine/device wallet
type SubWallet struct {
    ID              string    `json:"id"`
    MasterWalletID  string    `json:"masterWalletId"`
    DeviceID        string    `json:"deviceId"`
    PublicKey       string    `json:"publicKey"`
    Balance         float64   `json:"balance"`
    DailyLimit      float64   `json:"dailyLimit"`
    DailySpent      float64   `json:"dailySpent"`
    LastReset       time.Time `json:"lastReset"`
    AllowedPaymentTypes []string `json:"allowedPaymentTypes"`
    Status          string    `json:"status"`
}

// Payment represents a transaction
type Payment struct {
    ID              string    `json:"id"`
    WalletType      string    `json:"walletType"`      // solana, hedera
    SenderWallet    string    `json:"senderWallet"`    // wallet ID
    ReceiverWallet  string    `json:"receiverWallet"`  // wallet ID
    Amount          float64   `json:"amount"`
    PaymentType     string    `json:"paymentType"`     // charging, parking, etc
    Status          string    `json:"status"`
    TransactionHash string    `json:"transactionHash"`
    Timestamp       time.Time `json:"timestamp"`
    AutomatedTx     bool      `json:"automatedTx"`     // true for M2M transactions
}

// TimeSeriesData represents IoT sensor data
type TimeSeriesData struct {
    DeviceID        string    `json:"deviceId"`
    Timestamp       time.Time `json:"timestamp"`
    DataType        string    `json:"dataType"`
    Value           string    `json:"value"`
    QuantumSignature string   `json:"quantumSignature"`
}

// RegisterDevice registers a new ARM device
func (c *QuantumSecureContract) RegisterDevice(ctx contractapi.TransactionContextInterface,
    deviceID string, deviceType string, publicKey string, quantumKey string) error {

    device := Device{
        ID:           deviceID,
        Type:         deviceType,
        PublicKey:    publicKey,
        QuantumKey:   quantumKey,
        LastVerified: time.Now(),
        Status:       "active",
    }

    deviceJSON, err := json.Marshal(device)
    if err != nil {
        return fmt.Errorf("failed to marshal device: %v", err)
    }

    err = ctx.GetStub().PutState(deviceID, deviceJSON)
    if err != nil {
        return fmt.Errorf("failed to register device: %v", err)
    }

    return nil
}

// VerifyDevice verifies an ARM device
func (c *QuantumSecureContract) VerifyDevice(ctx contractapi.TransactionContextInterface,
    deviceID string, quantumSignature string) (bool, error) {

    deviceJSON, err := ctx.GetStub().GetState(deviceID)
    if err != nil {
        return false, fmt.Errorf("failed to read device: %v", err)
    }

    if deviceJSON == nil {
        return false, fmt.Errorf("device does not exist")
    }

    var device Device
    err = json.Unmarshal(deviceJSON, &device)
    if err != nil {
        return false, err
    }

    // Update verification timestamp
    device.LastVerified = time.Now()
    updatedDeviceJSON, err := json.Marshal(device)
    if err != nil {
        return false, err
    }

    err = ctx.GetStub().PutState(deviceID, updatedDeviceJSON)
    if err != nil {
        return false, err
    }

    return true, nil
}

// RecordCommunication records a secure communication
func (c *QuantumSecureContract) RecordCommunication(ctx contractapi.TransactionContextInterface,
    commType string, sender string, receiver string, quantumSignature string,
    messageHash string, protocol string) error {

    comm := Communication{
        ID:              ctx.GetStub().GetTxID(),
        Type:            commType,
        Sender:          sender,
        Receiver:        receiver,
        QuantumSignature: quantumSignature,
        Timestamp:       time.Now(),
        MessageHash:     messageHash,
        Protocol:        protocol,
    }

    commJSON, err := json.Marshal(comm)
    if err != nil {
        return err
    }

    return ctx.GetStub().PutState(comm.ID, commJSON)
}

// StoreTimeSeriesData stores IoT sensor data
func (c *QuantumSecureContract) StoreTimeSeriesData(ctx contractapi.TransactionContextInterface,
    deviceID string, dataType string, value string, quantumSignature string) error {

    // Verify device first
    verified, err := c.VerifyDevice(ctx, deviceID, quantumSignature)
    if err != nil || !verified {
        return fmt.Errorf("device verification failed")
    }

    data := TimeSeriesData{
        DeviceID:        deviceID,
        Timestamp:       time.Now(),
        DataType:        dataType,
        Value:           value,
        QuantumSignature: quantumSignature,
    }

    dataJSON, err := json.Marshal(data)
    if err != nil {
        return err
    }

    // Create composite key for time series data
    timeKey := fmt.Sprintf("%s-%d", deviceID, time.Now().UnixNano())
    return ctx.GetStub().PutState(timeKey, dataJSON)
}

// CreateMasterWallet initializes a new master wallet
func (c *QuantumSecureContract) CreateMasterWallet(ctx contractapi.TransactionContextInterface,
    walletType string, publicKey string, dailyLimit float64) (*MasterWallet, error) {

    masterWallet := &MasterWallet{
        ID:          ctx.GetStub().GetTxID(),
        WalletType:  walletType,
        PublicKey:   publicKey,
        Balance:     0,
        Status:      "active",
        SubWallets:  make([]string, 0),
        DailyLimit:  dailyLimit,
        CreatedAt:   time.Now(),
    }

    walletJSON, err := json.Marshal(masterWallet)
    if err != nil {
        return nil, err
    }

    err = ctx.GetStub().PutState(masterWallet.ID, walletJSON)
    if err != nil {
        return nil, err
    }

    return masterWallet, nil
}

// CreateSubWallet creates a new sub-wallet for a device
func (c *QuantumSecureContract) CreateSubWallet(ctx contractapi.TransactionContextInterface,
    masterWalletID string, deviceID string, publicKey string, dailyLimit float64,
    allowedPaymentTypes []string) (*SubWallet, error) {

    // Verify master wallet exists
    masterWalletJSON, err := ctx.GetStub().GetState(masterWalletID)
    if err != nil {
        return nil, fmt.Errorf("failed to read master wallet: %v", err)
    }
    if masterWalletJSON == nil {
        return nil, fmt.Errorf("master wallet does not exist")
    }

    var masterWallet MasterWallet
    err = json.Unmarshal(masterWalletJSON, &masterWallet)
    if err != nil {
        return nil, err
    }

    // Create sub-wallet
    subWallet := &SubWallet{
        ID:                 ctx.GetStub().GetTxID(),
        MasterWalletID:     masterWalletID,
        DeviceID:           deviceID,
        PublicKey:          publicKey,
        Balance:            0,
        DailyLimit:         dailyLimit,
        DailySpent:         0,
        LastReset:          time.Now(),
        AllowedPaymentTypes: allowedPaymentTypes,
        Status:             "active",
    }

    subWalletJSON, err := json.Marshal(subWallet)
    if err != nil {
        return nil, err
    }

    err = ctx.GetStub().PutState(subWallet.ID, subWalletJSON)
    if err != nil {
        return nil, err
    }

    // Update master wallet's sub-wallet list
    masterWallet.SubWallets = append(masterWallet.SubWallets, subWallet.ID)
    updatedMasterWalletJSON, err := json.Marshal(masterWallet)
    if err != nil {
        return nil, err
    }

    err = ctx.GetStub().PutState(masterWalletID, updatedMasterWalletJSON)
    if err != nil {
        return nil, err
    }

    return subWallet, nil
}

// ProcessM2MPayment handles autonomous machine-to-machine payments
func (c *QuantumSecureContract) ProcessM2MPayment(ctx contractapi.TransactionContextInterface,
    senderWalletID string, receiverWalletID string, amount float64,
    paymentType string) (*Payment, error) {

    // Get sender sub-wallet
    senderJSON, err := ctx.GetStub().GetState(senderWalletID)
    if err != nil || senderJSON == nil {
        return nil, fmt.Errorf("sender wallet not found")
    }
    var sender SubWallet
    err = json.Unmarshal(senderJSON, &sender)
    if err != nil {
        return nil, err
    }

    // Validate payment type
    validPaymentType := false
    for _, allowed := range sender.AllowedPaymentTypes {
        if allowed == paymentType {
            validPaymentType = true
            break
        }
    }
    if !validPaymentType {
        return nil, fmt.Errorf("payment type not allowed for this wallet")
    }

    // Check daily limit
    if sender.DailySpent + amount > sender.DailyLimit {
        return nil, fmt.Errorf("daily limit exceeded")
    }

    // Reset daily spent if needed
    if time.Since(sender.LastReset) > 24*time.Hour {
        sender.DailySpent = 0
        sender.LastReset = time.Now()
    }

    // Create and record payment
    payment := &Payment{
        ID:             ctx.GetStub().GetTxID(),
        WalletType:     "solana", // or get from master wallet
        SenderWallet:   senderWalletID,
        ReceiverWallet: receiverWalletID,
        Amount:         amount,
        PaymentType:    paymentType,
        Status:         "pending",
        Timestamp:      time.Now(),
        AutomatedTx:    true,
    }

    // Update sender's daily spent
    sender.DailySpent += amount
    senderJSON, err = json.Marshal(sender)
    if err != nil {
        return nil, err
    }
    err = ctx.GetStub().PutState(senderWalletID, senderJSON)
    if err != nil {
        return nil, err
    }

    // Record payment
    paymentJSON, err := json.Marshal(payment)
    if err != nil {
        return nil, err
    }
    err = ctx.GetStub().PutState(payment.ID, paymentJSON)
    if err != nil {
        return nil, err
    }

    return payment, nil
}

// ProcessPayment processes a manual payment using specified wallet
func (c *QuantumSecureContract) ProcessPayment(ctx contractapi.TransactionContextInterface,
    commID string, walletType string, amount float64) error {

    commJSON, err := ctx.GetStub().GetState(commID)
    if err != nil {
        return err
    }

    var comm Communication
    err = json.Unmarshal(commJSON, &comm)
    if err != nil {
        return err
    }

    payment := Payment{
        WalletType:      walletType,
        Amount:          amount,
        Status:          "pending",
        TransactionHash: "", // To be updated after processing
    }

    comm.PaymentInfo = &payment
    updatedCommJSON, err := json.Marshal(comm)
    if err != nil {
        return err
    }

    return ctx.GetStub().PutState(commID, updatedCommJSON)
}

func main() {
    chaincode, err := contractapi.NewChaincode(&QuantumSecureContract{})
    if err != nil {
        fmt.Printf("Error creating chaincode: %s", err.Error())
        return
    }

    if err := chaincode.Start(); err != nil {
        fmt.Printf("Error starting chaincode: %s", err.Error())
    }
}