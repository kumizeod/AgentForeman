param([string]$ProjectPath = "")
$ErrorActionPreference = "Stop"
$RepoPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoPath
try {
    $PythonPath = Join-Path $RepoPath ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $PythonPath)) {
        $Launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($Launcher) { & $Launcher.Source -3 -m venv (Join-Path $RepoPath ".venv") }
        else { & python -m venv (Join-Path $RepoPath ".venv") }
        if ($LASTEXITCODE -ne 0) { throw "Install Python 3.11+ from python.org, then retry." }
    }
    & $PythonPath -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ is required'"
    if ($LASTEXITCODE -ne 0) { throw "The virtual environment needs Python 3.11+." }
    & $PythonPath -m pip install -e ".[demo,desktop]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check the network and retry." }
    if (-not $ProjectPath) { $ProjectPath = Join-Path $RepoPath "my-experiment" }
    $ProjectPath = [System.IO.Path]::GetFullPath($ProjectPath)
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectPath "foreman.toml"))) {
        # init refuses nonempty directories, protecting existing project files.
        & $PythonPath -m agentforeman init $ProjectPath
        if ($LASTEXITCODE -ne 0) { throw "Project initialization failed; existing files were not overwritten." }
    }
    Write-Host "Opening AgentForeman. Choose Quick Start / connection center. Keep this window open."
    & $PythonPath -m agentforeman ui --project $ProjectPath --port 0
    if ($LASTEXITCODE -ne 0) { throw "The console stopped with an error." }
} catch {
    Write-Host $_ -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}
