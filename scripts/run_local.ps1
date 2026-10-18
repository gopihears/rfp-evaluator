<#
  Run the RFP Evaluator locally.

  Usage (from the repository root):
    .\scripts\run_local.ps1          # real OpenAI key entered in the app at run time
    .\scripts\run_local.ps1 -Demo    # offline demo mode, no API key needed
    .\scripts\run_local.ps1 -Port 8600
#>
param(
    [switch]$Demo,
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"

Write-Host "Installing dependencies..."
& $py -m pip install -q -r requirements.txt

Write-Host "Preparing the database and sample criteria..."
& $py -m core.seed

if (-not (Test-Path "data\synthetic_rfps\manifest.json")) {
    Write-Host "Generating the synthetic RFP PDFs..."
    & $py -m scripts.make_synthetic_pdfs
}

if ($Demo) {
    $env:RFP_FAKE_LLM = "1"
    Write-Host "Demo mode: offline scorer, no API key needed."
} else {
    Remove-Item Env:\RFP_FAKE_LLM -ErrorAction SilentlyContinue
}

Write-Host "Starting on http://localhost:$Port"
& $py -m streamlit run app.py --server.port $Port --browser.gatherUsageStats false
