FROM python:3.9-slim

# Set environment variables with default values
ENV PYTHONPATH=/app
ENV LD_LIBRARY_PATH=/app/sip_connect/PQClean/crypto_sign/falcon-1024/aarch64:/app/sip_connect/kyber/ref

# If you need to append to these variables later, use a separate ENV statement
ENV PYTHONPATH="${PYTHONPATH}:/additional/path"
ENV LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:/additional/path"

# Install necessary build tools
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    make \
    cmake \
    git \
    gcc-aarch64-linux-gnu \
    binutils-aarch64-linux-gnu \
    binutils \
    qemu-user \
    qemu-user-static \
    ninja-build \
    libblas-dev \
    liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
COPY requirements/ requirements/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY . .

# Build Falcon-1024 for ARM64
RUN cd sip_connect/PQClean/crypto_sign/falcon-1024/aarch64 && \
    sed -i 's/CC=arm-linux-gnu-gcc/CC=aarch64-linux-gnu-gcc/' Makefile && \
    make clean && \
    make VERBOSE=1 || (echo "Falcon-1024 build failed" && exit 1)

# Build Kyber
RUN cd sip_connect/kyber/ref && \
    make clean && \
    make VERBOSE=1 || (echo "Kyber build failed" && exit 1)

# Set executable permissions
RUN chmod +x quantum_services_init.py quantum_msp_init.py

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import requests; requests.get('http://localhost:8000/health')" || exit 1

# Start the Python services
CMD ["sh", "-c", "python quantum_services_init.py && python quantum_msp_init.py"]