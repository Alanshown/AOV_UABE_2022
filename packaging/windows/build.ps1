[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$ExpectedCommit)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '../..'))
$actual = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actual -ne $ExpectedCommit) { throw "Refusing to build unexpected commit $actual" }
$changes = @(git status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $changes.Count) { throw 'Build requires a clean checkout' }
$py = if (Test-Path '.release-venv/Scripts/python.exe') { '.release-venv/Scripts/python.exe' } else { 'python' }
& $py -c 'import struct,sys; assert struct.calcsize("P") == 8 and sys.version_info[:2] == (3, 12), "Requires Windows Python 3.12 x64"'
if ($LASTEXITCODE) { throw 'Python version check failed' }
& $py -m pip install -r requirements.txt 'pyinstaller==6.14.2' 'pytest==8.3.5'
if ($LASTEXITCODE) { throw 'Could not install build dependencies' }
& $py -m pytest -q
if ($LASTEXITCODE) { throw 'Source regression tests failed' }
& $py -m PyInstaller --noconfirm --clean --windowed --onedir --name AOV_UABE_2022 --icon icon.ico --version-file version_info.txt `
  --add-data 'icon.ico:.' --add-data 'Settings.json:.' --add-data 'lang.json:.' `
  --add-data 'AssetbundleUtils/UnityPy_AOV/resources:AssetbundleUtils/UnityPy_AOV/resources' `
  --add-data 'AssetbundleUtils/UnityPy_AOV/helpers/global-metadata.dat:AssetbundleUtils/UnityPy_AOV/helpers' `
  --collect-all tkinterdnd2 --collect-all archspec --collect-all astc_encoder --hidden-import pyfmodex main.py
if ($LASTEXITCODE) { throw 'PyInstaller failed' }
$compiler = @((Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6/ISCC.exe'), (Join-Path $env:ProgramFiles 'Inno Setup 6/ISCC.exe')) |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (!$compiler) {
    $compiler = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
}
if (!$compiler) { throw 'Install Inno Setup 6 before running this script' }
& $compiler (Join-Path $PSScriptRoot 'setup.iss')
if ($LASTEXITCODE) { throw 'Inno Setup failed' }
$installer = Get-Item './artifacts/AOV_UABE_2022-2.5-Setup-x64.exe'
$app = Get-Item './dist/AOV_UABE_2022/AOV_UABE_2022.exe'
if ($app.VersionInfo.FileVersion -ne '2.5' -or $app.VersionInfo.ProductVersion -ne '2.5') { throw 'App version does not match 2.5' }
if ($installer.VersionInfo.FileMajorPart -ne 2 -or $installer.VersionInfo.FileMinorPart -ne 5) { throw 'Installer version does not match 2.5' }
$sha = (Get-FileHash $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
@("commit: $actual", "runtime: $(& $py --version)", "windows: $([Environment]::OSVersion.VersionString)") | Set-Content -Encoding ascii './artifacts/SOURCE-WINDOWS.txt'
"$sha  $($installer.Name)" | Set-Content -Encoding ascii './artifacts/SHA256SUMS.txt'
