# RailPilot static security scan (read-only; does not execute scanned code)
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools\security_scan.ps1 -Path <repo>
#   powershell -ExecutionPolicy Bypass -File tools\security_scan.ps1 -Path <repo> -OutFile scan.txt
# Notes:
#   - Prints only severity/rule/file/line, never the matched content (avoids leaking secrets).
#   - Heuristic scan, not a security proof; review every HIGH manually.

param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [string]$OutFile = "",
    [switch]$IncludeDocs
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $Path).Path
$exts = @(".py",".js",".ts",".tsx",".jsx",".json",".yaml",".yml",".ps1",".html",".cs",".java",".go",".rs")
if ($IncludeDocs) { $exts += @(".md",".txt") }

$skipDirs = @(".git","node_modules","dist","build","vendor","site-packages","__pycache__",".venv","venv","target","bin","obj")
$files = Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $rel = $_.FullName.Substring($root.Length).TrimStart('\')
        $parts = $rel -split '\\'
        $ext = $_.Extension.ToLower()
        -not ($parts | Where-Object { $skipDirs -contains $_ }) -and
        $exts -contains $ext -and
        $_.Name -ne "security_scan.ps1" -and
        $_.Name -ne ".gitignore"
    }

$rules = @(
    @{Severity="HIGH"; Code="SECRET_PASSWORD"; Regex="(?i)(password|passwd|pwd)\s*[:=]"},
    @{Severity="HIGH"; Code="TLS_VERIFY_OFF"; Regex="(?i)verify\s*=\s*False"},
    @{Severity="HIGH"; Code="HTTP_PLAINTEXT"; Regex="(?i)(requests\.(get|post)\(\s*[\x22\x27]http://|url\s*=\s*[\x22\x27]http://|fetch\(\s*[\x22\x27]http://)"},
    @{Severity="HIGH"; Code="RAIL_DEVICEID"; Regex="RAIL_DEVICEID|RAIL_EXPIRATION"},
    @{Severity="HIGH"; Code="ANTI_DETECT"; Regex="(?i)navigator\.webdriver|AutomationControlled|anti_detect|cdc_"},
    @{Severity="HIGH"; Code="THIRD_PARTY_LOOKUP"; Regex="(?i)ip-api\.com|whois\.pconline|fofa\.info"},
    @{Severity="HIGH"; Code="PRIVATE_KEY"; Regex="BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY"},
    @{Severity="HIGH"; Code="PLAINTEXT_SESSION"; Regex="(?i)(json\.dump.*cookie|write_text.*cookie|storage_state|user_data_dir|session_cookies|otn_cookies|browser_state)"},
    @{Severity="MEDIUM"; Code="PII_FIELD"; Regex="(?i)id_no|passenger_id_no|mobile_no|phone_no"},
    @{Severity="MEDIUM"; Code="TELEMETRY"; Regex="(?i)sentry|posthog|mixpanel|google-analytics|bugsnag"},
    @{Severity="MEDIUM"; Code="DANGEROUS_DYNAMIC"; Regex="(?i)\beval\s*\(|\bexec\s*\(|pickle\.load|yaml\.load"},
    @{Severity="MEDIUM"; Code="SUBPROCESS"; Regex="(?i)\bsubprocess\b|os\.system|shell\s*=\s*True"},
    @{Severity="MEDIUM"; Code="AUTO_UPDATE"; Regex="(?i)update_url|electron-updater|chromedriver.*download|auto_update"},
    @{Severity="MEDIUM"; Code="CAPTCHA_BYPASS"; Regex="(?i)(bypass|solve|crack|recognize).{0,20}(captcha|slider)|(captcha|slider).{0,20}(bypass|solve|crack|recognize)"}
)

$findings = New-Object System.Collections.Generic.List[object]
foreach ($rule in $rules) {
    $hits = $files | Select-String -Pattern $rule.Regex -ErrorAction SilentlyContinue |
        Where-Object {
            $line = $_.Line
            $path = $_.Path
            $isComment = $line -match '^\s*(#|//|/\*|\*)'
            $isDenial = $line -match '(?i)(allow|deny|forbid)_(anti_detect|captcha_bypass|multi_account|proxy)\s*:\s*false'
            $isRedLine = $line -match '(?i)RED_LINE|red-line'
            $isRedactModule = $path -match '(?i)redact\.py|test_redact\.py'
            $isAcl = $line -match '(?i)icacls'
            $skip =
                $isComment -or $isDenial -or
                ($rule.Code -eq "ANTI_DETECT" -and $isRedLine) -or
                ($rule.Code -eq "RAIL_DEVICEID" -and ($isRedactModule -or $isRedLine)) -or
                ($rule.Code -eq "CAPTCHA_BYPASS" -and $isRedLine) -or
                ($rule.Code -eq "SUBPROCESS" -and $isAcl)
            -not $skip
        }
    $count = 0
    foreach ($hit in $hits) {
        if ($count -ge 200) { break }
        $rel = $hit.Path.Substring($root.Length).TrimStart('\')
        $findings.Add([pscustomobject]@{
            severity = $rule.Severity
            code     = $rule.Code
            file     = $rel
            line     = $hit.LineNumber
        })
        $count++
    }
}

$findings = $findings | Sort-Object severity, code, file, line
$high = @($findings | Where-Object { $_.severity -eq "HIGH" }).Count
$med  = @($findings | Where-Object { $_.severity -eq "MEDIUM" }).Count

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("# RailPilot security scan")
$lines.Add("# root: $root")
$lines.Add("# files: $($files.Count); high: $high; medium: $med")
$lines.Add("# note: heuristic only; review every HIGH manually")
$lines.Add("")
$lines.Add("severity`tcode`tfile`tline")
foreach ($f in $findings) {
    $lines.Add(("{0}`t{1}`t{2}`t{3}" -f $f.severity, $f.code, $f.file, $f.line))
}

if ($OutFile) {
    $lines | Out-File -LiteralPath $OutFile -Encoding UTF8
    Write-Host "written: $OutFile"
} else {
    $lines | ForEach-Object { Write-Host $_ }
}

if ($high -gt 0) { exit 1 } else { exit 0 }
