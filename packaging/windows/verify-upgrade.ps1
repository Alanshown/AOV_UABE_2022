[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '../..'))
$artifactPaths = (Resolve-Path './artifacts').Path
$candidate = Join-Path $artifactPaths 'AOV_UABE_2022-2.4-Setup-x64.exe'
$work = Join-Path $env:TEMP ('aov-uabe-upgrade-' + [guid]::NewGuid())
New-Item -ItemType Directory $work | Out-Null
$release = 'https://github.com/Alanshown/AOV_UABE_2022/releases/download'
$legacySetup = Join-Path $work '2.3.6.exe'
$baselineSetup = Join-Path $work '2.4-original.exe'
$old = '{349E126A-338F-480D-B360-B76A97BB88CB}'
$current = 'AOV_UABE_2022'
$defaultInstall = Join-Path $env:LOCALAPPDATA 'Programs/AOV_UABE_2022'
$programs = [Environment]::GetFolderPath('Programs')
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$legacyStart = Join-Path $programs 'AOV Old Custom Menu\AOV UABE 2022.lnk'
$legacyDesktop = Join-Path $desktop 'AOV UABE 2022.lnk'
$currentStart = Join-Path $programs 'AOV_UABE_2022.lnk'
$appExe = 'AOV_UABE_2022.exe'
$shell = New-Object -ComObject WScript.Shell

function PathKey([string]$path) { [IO.Path]::GetFullPath($path).TrimEnd('\') }
function AssertEqual([string]$actual, [string]$expected, [string]$label) {
    if ($actual -ne $expected) { throw "${label}: got <$actual>, expected <$expected>" }
}
function RegistrySubkey([string]$identity) {
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\${identity}_is1"
}
function CheckInstall([string]$identity, [string]$destination, [string]$version) {
    $entry = Get-ItemProperty -LiteralPath (RegistrySubkey $identity)
    AssertEqual (PathKey $entry.'Inno Setup: App Path') (PathKey $destination) 'Registered install path'
    AssertEqual $entry.DisplayVersion $version 'Registered version'
    AssertEqual (Get-Item -LiteralPath (Join-Path $destination $appExe)).VersionInfo.ProductVersion $version 'Installed EXE version'
    return $entry
}
function DownloadPinned([string]$url,[string]$dest,[string]$expected) {
    Invoke-WebRequest -Uri $url -OutFile $dest -TimeoutSec 90
    AssertEqual (Get-FileHash $dest -Algorithm SHA256).Hash $expected 'Published installer SHA-256'
}
function RunSetup([string]$exe,[string]$label,[string[]]$extra=@()) {
    $log = Join-Path $artifactPaths "$label.log"
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCANCEL', "/LOG=`"$log`"") + $extra
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -Wait -PassThru
    if ($p.ExitCode -ne 0) { Get-Content $log -Tail 65; throw "$label returned exit code $($p.ExitCode)" }
}
function CheckLink([string]$link,[string]$destination) {
    if (!(Test-Path -LiteralPath $link -PathType Leaf)) { throw "Missing shortcut <$link>" }
    $shortcut = $shell.CreateShortcut($link)
    AssertEqual (PathKey $shortcut.TargetPath) (PathKey (Join-Path $destination $appExe)) 'Shortcut EXE target'
    AssertEqual (PathKey $shortcut.WorkingDirectory) (PathKey $destination) 'Shortcut working dir'
}
function DisturbLink([string]$link,[string]$destination) {
    if (!(Test-Path -LiteralPath $link -PathType Leaf)) { throw "Missing existing shortcut <$link>" }
    $shortcut = $shell.CreateShortcut($link)
    # Leave the target intact but detect whether the installer truly rewrites it.
    $shortcut.WorkingDirectory = $work
    $shortcut.Save()
    AssertEqual (PathKey ($shell.CreateShortcut($link).WorkingDirectory)) (PathKey $work) 'Test link disturbance'
}
function ExpectMarker([string]$path){ AssertEqual (Get-Content -LiteralPath $path -Raw) 'keep user file' 'User file preservation' }

foreach($id in @($old,$current)) {
    if (Test-Path -LiteralPath (RegistrySubkey $id)) { throw 'Test requires a clean Windows runner' }
}
if (Test-Path -LiteralPath $defaultInstall) { throw 'Expected clean default location' }
DownloadPinned "$release/latest2.1/AOV_UABE_2022-2.3.6-Setup-x64.exe" $legacySetup '9519ba46e5a99e6618da203e26d8420111cee53acd664a4a2b51c73d61c37a77'

$legacyPath = Join-Path $work 'Legacy nondefault dir'
RunSetup $legacySetup 'upgrade-1-legacy-setup' @("/DIR=`"$legacyPath`"", '/GROUP="AOV Old Custom Menu"', '/TASKS=desktopicon', '/LANG=english')
$before = CheckInstall $old $legacyPath '2.3.6'
# Verify the actual old shortcuts exist. They predate the new setup.
foreach($lnk in @($legacyStart,$legacyDesktop)) {
    AssertEqual (PathKey ($shell.CreateShortcut($lnk).TargetPath)) (PathKey (Join-Path $legacyPath $appExe)) 'Legacy shortcut EXE'
    DisturbLink $lnk $legacyPath
}
$marker = Join-Path $legacyPath 'my-project-user-note.txt'
[IO.File]::WriteAllText($marker,'keep user file')
RunSetup $candidate 'upgrade-2-legacy-to-24'
$after = CheckInstall $old $legacyPath '2.4'
AssertEqual $after.UninstallString $before.UninstallString 'Reused legacy uninstall log'
if (Test-Path -LiteralPath (RegistrySubkey $current)) { throw 'Duplicate registration from a legacy upgrade' }
if (Test-Path -LiteralPath $defaultInstall) { throw 'Setup created an install in the default dir instead of upgrading' }
foreach($lnk in @($legacyStart,$legacyDesktop)) { CheckLink $lnk $legacyPath }
ExpectMarker $marker
Write-Host 'PASS: published 2.3.6 upgraded in custom directory, verified 2.4, retained one registered uninstall, rewrote legacy Start Menu and desktop links, retained user file.'

# Remove the test install using its registered uninstaller, then create a real
# side-by-side state with the current ID and a fresh, unmodified 2.3.6 install.
$uninstall = $after.QuietUninstallString
if ($uninstall -notmatch '^"([^"]+)"') { throw 'Could not read legacy uninstall executable' }
$uninstallExe = $Matches[1]
if ((PathKey (Split-Path -Parent $uninstallExe)) -ne (PathKey $legacyPath)) { throw 'Uninstall executable is outside the dedicated test directory' }
RunSetup $uninstallExe 'upgrade-3-remove-test-install'
if (Test-Path -LiteralPath (RegistrySubkey $old)) { throw 'Legacy uninstall registration remained' }
$currentPath = Join-Path $work 'Existing 2.4 nondefault dir'
$baseline = $candidate
try {
    Invoke-WebRequest -Uri "$release/latest2.4/AOV_UABE_2022-2.4-Setup-x64.exe" -OutFile $baselineSetup -TimeoutSec 90
    if ((Get-FileHash $baselineSetup -Algorithm SHA256).Hash -eq '7b6429d365d38976e16e385af34dd3b59323f61812c20d3051c1a214e740e9d6') {
        $baseline = $baselineSetup
    }
} catch { Write-Host 'Original 2.4 asset could not be downloaded.' }
if ($baseline -eq $baselineSetup) {
    Write-Host 'Current-identity fixture: verified original published 2.4 installer.'
} else {
    Write-Host 'Current-identity fixture: clean install of the candidate (original release asset was superseded or unavailable).'
}
RunSetup $baseline 'upgrade-4-current-24-setup' @("/DIR=`"$currentPath`"")
$currentBefore = CheckInstall $current $currentPath '2.4'
CheckLink $currentStart $currentPath
DisturbLink $currentStart $currentPath

$untouchedLegacyPath = Join-Path $work 'Legacy side-by-side nondefault dir'
RunSetup $legacySetup 'upgrade-5-side-by-side-legacy-setup' @("/DIR=`"$untouchedLegacyPath`"", '/GROUP="AOV Old Custom Menu"', '/TASKS=desktopicon', '/LANG=english')
$null = CheckInstall $old $untouchedLegacyPath '2.3.6'
$legacyHash = (Get-FileHash (Join-Path $untouchedLegacyPath $appExe) -Algorithm SHA256).Hash
$legacyLinks = @{}
foreach ($link in @($legacyStart,$legacyDesktop)) { $legacyLinks[$link] = (Get-FileHash -LiteralPath $link -Algorithm SHA256).Hash }
RunSetup $candidate 'upgrade-6-prefer-current-24'
$currentAfter = CheckInstall $current $currentPath '2.4'
AssertEqual $currentAfter.UninstallString $currentBefore.UninstallString 'Reused current uninstall log'
CheckLink $currentStart $currentPath
$null = CheckInstall $old $untouchedLegacyPath '2.3.6'
foreach ($link in @($legacyStart,$legacyDesktop)) { AssertEqual (Get-FileHash -LiteralPath $link -Algorithm SHA256).Hash $legacyLinks[$link] 'Legacy side-by-side shortcut unchanged' }
AssertEqual (Get-FileHash (Join-Path $untouchedLegacyPath $appExe) -Algorithm SHA256).Hash $legacyHash 'Legacy side-by-side EXE unchanged'
if (Test-Path -LiteralPath $defaultInstall) { throw 'Unexpected default directory created' }
Write-Host 'PASS: existing 2.4 had priority over the side-by-side 2.3.6 identity; current Start Menu link replaced; legacy EXE and shortcuts unchanged.'
@(
    'PASS: published 2.3.6 upgraded in custom directory without /DIR.'
    'PASS: reused legacy registration; preserved a user file; rewrote existing Start Menu and desktop shortcuts.'
    'PASS: existing 2.4 took priority with both identities present; kept the older EXE and shortcuts untouched.'
    "Baseline: $(Split-Path -Leaf $baseline)"
) | Set-Content -Encoding utf8 (Join-Path $artifactPaths 'UPGRADE-VERIFICATION.txt')
Write-Output $currentPath
