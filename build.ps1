param([string]$DistPath = 'dist')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# PyInstaller writes progress to stderr; Stop mode would treat it as a terminating error.
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --name DevToolNext --onedir --distpath $DistPath --add-data 'devtoolnext/web;devtoolnext/web' --collect-submodules serial.urlhandler launch.py 2>&1 | Write-Host
$code = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($code -ne 0) { throw "Build failed with exit code $code. Close DevToolNext.exe in the target directory first." }
