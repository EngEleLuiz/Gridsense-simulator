# Phase 2 Setup — Automated for Windows PowerShell
#
# This script automates the entire Phase 2 setup:
# - Creates virtual environment
# - Installs dependencies
# - Creates data directories
# - Verifies Docker is installed and running
# - Starts Kafka containers
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/setup_phase2.ps1
#
# Or just run from PowerShell if you have execution policy set:
#   .\scripts\setup_phase2.ps1

Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "GridSense Simulator — Phase 2 Setup" -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# Function to print colored output
function Write-Step {
    param([string]$Message)
    Write-Host "→ $Message" -ForegroundColor Yellow
}

function Write-Success {
    param([string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "✗ $Message" -ForegroundColor Red
}

# Step 1: Check if Docker is installed
Write-Step "Checking Docker installation..."
$dockerVersion = docker --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "Docker is not installed or not in PATH"
    Write-Host ""
    Write-Host "Install Docker Desktop from: https://www.docker.com/products/docker-desktop" -ForegroundColor Magenta
    exit 1
}
Write-Success "Docker found: $dockerVersion"
Write-Host ""

# Step 2: Check if Docker daemon is running
Write-Step "Checking if Docker daemon is running..."
$dockerInfo = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "Docker daemon is not running"
    Write-Host ""
    Write-Host "Please start Docker Desktop and run this script again." -ForegroundColor Magenta
    exit 1
}
Write-Success "Docker daemon is running"
Write-Host ""

# Step 3: Check Python version
Write-Step "Checking Python installation..."
$pythonVersion = python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "Python is not installed or not in PATH"
    exit 1
}
Write-Success "Python found: $pythonVersion"
Write-Host ""

# Step 4: Create virtual environment
Write-Step "Creating Python virtual environment..."
if (Test-Path ".venv") {
    Write-Success "Virtual environment already exists"
} else {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Failed to create virtual environment"
        exit 1
    }
    Write-Success "Virtual environment created"
}
Write-Host ""

# Step 5: Activate virtual environment
Write-Step "Activating virtual environment..."
& .\.venv\Scripts\Activate.ps1
if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "Failed to activate virtual environment"
    Write-Host "Try running manually: .\.venv\Scripts\Activate.ps1" -ForegroundColor Magenta
    exit 1
}
Write-Success "Virtual environment activated"
Write-Host ""

# Step 6: Install simulator dependencies
Write-Step "Installing simulator dependencies (this may take a minute)..."
cd simulator
pip install -e ".[dev,kafka]" -q
if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "Failed to install simulator dependencies"
    exit 1
}
Write-Success "Simulator dependencies installed"
cd ..
Write-Host ""

# Step 7: Install ingestion dependencies
Write-Step "Installing ingestion dependencies..."
pip install -r ingestion/requirements.txt -q
if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "Failed to install ingestion dependencies"
    exit 1
}
Write-Success "Ingestion dependencies installed"
Write-Host ""

# Step 8: Create data directories
Write-Step "Creating data directories..."
$dataDirs = @("data/bronze", "data/raw")
foreach ($dir in $dataDirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Success "Created directory: $dir"
    } else {
        Write-Success "Directory already exists: $dir"
    }
}
Write-Host ""

# Step 9: Start Kafka containers
Write-Step "Starting Kafka containers (this may take 20-30 seconds on first run)..."
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "Failed to start Kafka containers"
    Write-Host "Check if docker-compose.yml exists in the project root" -ForegroundColor Magenta
    exit 1
}
Write-Success "Kafka containers started"
Write-Host ""

# Step 10: Wait for Kafka to be ready
Write-Step "Waiting for Kafka to be healthy (this may take up to 30 seconds)..."
$maxAttempts = 30
$attempt = 0
$kafkaReady = $false

while ($attempt -lt $maxAttempts -and -not $kafkaReady) {
    $attempt++
    $status = docker compose ps --services --filter "status=running" 2>&1
    if ($status -match "gridsense-kafka" -and $status -match "gridsense-kafka-ui") {
        $kafkaReady = $true
    } else {
        Write-Host "  Waiting... ($attempt/$maxAttempts)" -ForegroundColor Gray
        Start-Sleep -Seconds 1
    }
}

if ($kafkaReady) {
    Write-Success "Kafka is ready!"
} else {
    Write-Error-Custom "Kafka did not become healthy in time"
    Write-Host "Run: docker compose logs" -ForegroundColor Magenta
    exit 1
}
Write-Host ""

# Step 11: Verify Kafka is accessible
Write-Step "Verifying Kafka is accessible..."
$pythonTest = @"
try:
    from confluent_kafka import Producer
    print('OK')
except:
    print('FAIL')
"@

$testConnect = python -c $pythonTest 2>&1
if ($testConnect -match "OK") {
    Write-Success "Kafka client library working"
} else {
    Write-Error-Custom "Kafka client library test failed"
    exit 1
}
Write-Host ""

# Final summary
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "✓ Phase 2 Setup Complete!" -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps (open new PowerShell terminals):" -ForegroundColor Cyan
Write-Host ""
Write-Host "Terminal 1 — Run the simulator:" -ForegroundColor Yellow
Write-Host '  python -m gridsense_sim.cli --network case14 --steps 200 --kafka --kafka-bootstrap-servers localhost:9092 -v' -ForegroundColor White
Write-Host ""
Write-Host "Terminal 2 — Consume and land to Parquet:" -ForegroundColor Yellow
Write-Host '  python ingestion/bronze_consumer.py --bootstrap-servers localhost:9092 --output-dir data/bronze -v' -ForegroundColor White
Write-Host ""
Write-Host "Terminal 3 — View Kafka UI:" -ForegroundColor Yellow
Write-Host "  Browser: http://localhost:8080" -ForegroundColor White
Write-Host ""
Write-Host "Terminal 4 — Query the data:" -ForegroundColor Yellow
Write-Host '  python ingestion_query.py' -ForegroundColor White
Write-Host ""
Write-Host "Or use the Makefile shortcuts:" -ForegroundColor Yellow
Write-Host "  make produce          (run simulator)" -ForegroundColor White
Write-Host "  make consume-bronze   (run consumer)" -ForegroundColor White
Write-Host "  make query-bronze     (verify data)" -ForegroundColor White
Write-Host "  make down             (stop Kafka)" -ForegroundColor White
Write-Host ""
