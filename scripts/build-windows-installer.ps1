$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Iss = Join-Path $Root "packaging/windows/open-llm-wiki-client.iss"
$Iscc = $env:ISCC_PATH

if (-not $Iscc) {
    throw "ISCC_PATH is not set. Point it to Inno Setup Compiler."
}

& $Iscc $Iss
Write-Output "Built Windows installer from $Iss"
