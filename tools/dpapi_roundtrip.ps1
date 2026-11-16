# DPAPI current-user roundtrip check (Windows only)
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools\dpapi_roundtrip.ps1
# What it proves:
#   1) CryptProtectData/CryptUnprotectData works for the current user.
#   2) The ciphertext does not contain the plaintext.
#   3) A tampered blob fails to decrypt.
# It does NOT prove another user cannot decrypt; do that manually with a second Windows account.

$ErrorActionPreference = "Stop"

try {
    Add-Type -AssemblyName System.Security
} catch {
    Write-Host "DPAPI check: FAILED (System.Security not available)"
    exit 1
}

$plain = "railpilot-dpapi-" + [Guid]::NewGuid().ToString("N")
$bytes = [Text.Encoding]::UTF8.GetBytes($plain)
$scope = [Security.Cryptography.DataProtectionScope]::CurrentUser

try {
    $blob = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, $scope)
} catch {
    Write-Host "DPAPI protect: FAILED ($($_.Exception.Message))"
    exit 1
}

try {
    $out = [Security.Cryptography.ProtectedData]::Unprotect($blob, $null, $scope)
    $roundtrip = [Text.Encoding]::UTF8.GetString($out)
} catch {
    Write-Host "DPAPI unprotect: FAILED ($($_.Exception.Message))"
    exit 1
}

$containsPlain = $false
$blobText = [Text.Encoding]::UTF8.GetString($blob)
if ($blobText.Contains($plain)) { $containsPlain = $true }

$tamperFailed = $false
try {
    $bad = [byte[]]$blob.Clone()
    $bad[$bad.Length - 1] = $bad[$bad.Length - 1] -bxor 0xFF
    [void][Security.Cryptography.ProtectedData]::Unprotect($bad, $null, $scope)
} catch {
    $tamperFailed = $true
}

Write-Host "DPAPI CurrentUser roundtrip: $([bool]($roundtrip -eq $plain))"
Write-Host "ciphertext contains plaintext: $containsPlain"
Write-Host "tamper detection: $tamperFailed"
Write-Host "blob bytes: $($blob.Length)"

if (($roundtrip -eq $plain) -and (-not $containsPlain) -and $tamperFailed) {
    exit 0
} else {
    exit 1
}
