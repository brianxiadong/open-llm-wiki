$ErrorActionPreference = "Stop"

$Target = if ($args.Length -gt 0) { $args[0] } else { "dist/confidential-client-binary/open-llm-wiki-client/open-llm-wiki-client.exe" }
$CertFile = $env:WINDOWS_SIGN_CERT_FILE
$CertPassword = $env:WINDOWS_SIGN_CERT_PASSWORD
$TimestampUrl = if ($env:WINDOWS_SIGN_TIMESTAMP_URL) { $env:WINDOWS_SIGN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }

if (-not $CertFile) {
    throw "WINDOWS_SIGN_CERT_FILE is not set"
}

signtool sign /f $CertFile /p $CertPassword /tr $TimestampUrl /td sha256 /fd sha256 $Target
Write-Output "Signed Windows client: $Target"
