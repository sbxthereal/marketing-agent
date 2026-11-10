[CmdletBinding()]
param([switch]$InstallChrome, [switch]$RunTests)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this script on Windows.' }
$PilotRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $PilotRoot
$env:PYTHONUTF8 = '1'
$PilotUv = Get-Command uv -ErrorAction SilentlyContinue
if ($PilotUv) { $PilotUvPath = $PilotUv.Source }
else {
    $PilotUvPath = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (-not (Test-Path -LiteralPath $PilotUvPath)) {
        throw 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and rerun setup. See WINDOWS.md.'
    }
}
$PilotPython = Join-Path $PilotRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PilotPython)) {
    & $PilotUvPath venv --python 3.14 .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Windows Python environment.' }
}
& $PilotPython -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,14) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'This lockfile targets Python 3.14. Move the existing .venv aside and rerun setup.' }
& $PilotUvPath pip sync --python $PilotPython requirements-windows.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path -LiteralPath 'config.json')) { Copy-Item -LiteralPath 'config.browser.example.json' -Destination 'config.json' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
New-Item -ItemType Directory -Force -Path (Join-Path $PilotRoot 'runtime') | Out-Null

# chmod is not an NTFS access-control boundary. Protect local credentials/profile.
$PilotIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$PilotSystem = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$PilotAdmins = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
foreach ($PilotRelative in @('.env', 'config.json', 'runtime')) {
    $PilotPath = Join-Path $PilotRoot $PilotRelative
    $PilotDirectory = (Get-Item -LiteralPath $PilotPath).PSIsContainer
    if ($PilotDirectory) { $PilotAcl = [System.Security.AccessControl.DirectorySecurity]::new() }
    else { $PilotAcl = [System.Security.AccessControl.FileSecurity]::new() }
    $PilotAcl.SetAccessRuleProtection($true, $false)
    $PilotAcl.SetOwner($PilotIdentity)
    foreach ($PilotSid in @($PilotIdentity, $PilotSystem, $PilotAdmins)) {
        if ($PilotDirectory) {
            $PilotRule = [System.Security.AccessControl.FileSystemAccessRule]::new($PilotSid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        } else { $PilotRule = [System.Security.AccessControl.FileSystemAccessRule]::new($PilotSid, 'FullControl', 'Allow') }
        $PilotAcl.AddAccessRule($PilotRule)
    }
    Set-Acl -LiteralPath $PilotPath -AclObject $PilotAcl
}

& $PilotPython -c 'from pilot.platform_support import chrome_executable; import sys; sys.exit(0 if chrome_executable() else 1)'
if ($LASTEXITCODE -ne 0 -and $InstallChrome) {
    & $PilotPython -m playwright install chrome
    if ($LASTEXITCODE -ne 0) { throw 'Chrome installation failed. Install Google Chrome, then rerun setup.' }
}
& $PilotPython -m pilot doctor
if ($LASTEXITCODE -ne 0) { throw 'Configuration check failed.' }
if ($RunTests) {
    $PilotPreviousTests = $env:PILOT_BROWSER_TESTS
    try {
        $env:PILOT_BROWSER_TESTS = '1'
        & $PilotPython -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) { throw 'Validation failed; do not start the scheduled worker.' }
    } finally { $env:PILOT_BROWSER_TESTS = $PilotPreviousTests }
}
Write-Host 'Installed. Configure .env and config.json, then follow WINDOWS.md. Discovery and publishing have not been enabled.'
