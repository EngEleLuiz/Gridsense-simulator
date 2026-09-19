# Phase 2 Setup - Windows PowerShell (Ultra-Simple)

Write-Host ""
Write-Host "GridSense Simulator - Phase 2 Setup" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Docker
Write-Host "1. Checking Docker..." -ForegroundColor Yellow
docker --version > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker not found. Install from https://docker.com" -ForegroundColor Red
    exit 1
}
Write-Host "   OK: Docker found" -ForegroundColor Green
Write-Host ""

# Step 2: Python
Write-Host "2. Checking Python..." -ForegroundColor Yellow
python --version > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Python not found" -ForegroundColor Red
    exit 1
}
Write-Host "   OK: Python found" -ForegroundColor Green
Write-Host ""

# Step 3: venv
Write-Host "3. Creating venv..." -ForegroundColor Yellow
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
Write-Host "   OK: venv created" -ForegroundColor Green
Write-Host ""

# Step 4: Activate
Write-Host "4. Activating venv..." -ForegroundColor Yellow
& .\.venv\Scripts\Activate.ps1
Write-Host "   OK: venv activated" -ForegroundColor Green
Write-Host ""

# Step 5: Install simulator
Write-Host "5. Installing simulator deps..." -ForegroundColor Yellow
cd simulator
pip install -e ".[dev,kafka]" -q
cd ..
Write-Host "   OK: simulator installed" -ForegroundColor Green
Write-Host ""

# Step 6: Install ingestion
Write-Host "6. Installing ingestion deps..." -ForegroundColor Yellow
pip install -r ingestion/requirements.txt -q
Write-Host "   OK: ingestion installed" -ForegroundColor Green
Write-Host ""

# Step 7: Create dirs
Write-Host "7. Creating data directories..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path "data/bronze" -Force | Out-Null
New-Item -ItemType Directory -Path "data/raw" -Force | Out-Null
Write-Host "   OK: directories created" -ForegroundColor Green
Write-Host ""

# Step 8: Start Kafka
Write-Host "8. Starting Kafka (Docker)..." -ForegroundColor Yellow
docker compose up -d > $null 2>&1
Write-Host "   OK: Kafka started" -ForegroundColor Green
Write-Host ""

# Step 9: Wait for Kafka
Write-Host "9. Waiting for Kafka..." -ForegroundColor Yellow
for ($i = 1; $i -le 30; $i++) {
    $status = docker compose ps --services --filter "status=running" 2>&1
    if ($status -match "gridsense-kafka") {
        Write-Host "   OK: Kafka is ready" -ForegroundColor Green
        break
    }
    Start-Sleep -Seconds 1
    Write-Host "   Waiting... ($i/30)" -ForegroundColor Gray
}
Write-Host ""

# Done
Write-Host "===================================" -ForegroundColor Green
Write-Host "SETUP COMPLETE!" -ForegroundColor Green
Write-Host "===================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next: Open NEW PowerShell terminals and run:" -ForegroundColor Cyan
Write-Host ""
Write-Host "Terminal 1 - Simulator:" -ForegroundColor Yellow
Write-Host "  python -m gridsense_sim.cli --network case14 --steps 200 --kafka --kafka-bootstrap-servers localhost:9092 -v" -ForegroundColor White
Write-Host ""
Write-Host "Terminal 2 - Consumer (while Terminal 1 runs):" -ForegroundColor Yellow
Write-Host "  python ingestion/bronze_consumer.py --bootstrap-servers localhost:9092 --output-dir data/bronze -v" -ForegroundColor White
Write-Host ""
Write-Host "Terminal 3 - Query (after both finish):" -ForegroundColor Yellow
Write-Host "  python ingestion_query.py" -ForegroundColor White
Write-Host ""
