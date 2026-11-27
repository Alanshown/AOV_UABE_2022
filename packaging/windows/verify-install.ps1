[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '../..'))
$setup = (Resolve-Path './artifacts/AOV_UABE_2022-2.4-Setup-x64.exe').Path
$install = Join-Path $env:TEMP ('aov-uabe-install-' + [guid]::NewGuid())
$installerProcess = Start-Process -FilePath $setup -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/DIR=`"$install`"") -PassThru -Wait
if ($installerProcess.ExitCode -ne 0) { throw "Installer returned $($installerProcess.ExitCode)" }
$exe = Get-Item (Join-Path $install 'AOV_UABE_2022.exe')
if ($exe.VersionInfo.ProductVersion -ne '2.4') { throw 'Installed EXE is not version 2.4' }
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AovWindowCapture {
  [StructLayout(LayoutKind.Sequential)]
  public struct Rect { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr handle, IntPtr dc, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr handle, out Rect bounds);
}
'@
$app = Start-Process -FilePath $exe.FullName -WorkingDirectory $install -PassThru
try {
    $found = $false
    for ($i=0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        $app.Refresh()
        if ($app.HasExited) { throw 'Installed app exited before opening its main window' }
        if ($app.MainWindowHandle -ne 0 -and $app.MainWindowTitle -match '2022 2\.4$') { $found = $true; break }
    }
    if (!$found) { throw "No visible 2.4 window; last title was $($app.MainWindowTitle)" }
    Start-Sleep -Seconds 2
    $bounds = New-Object AovWindowCapture+Rect
    if (![AovWindowCapture]::GetWindowRect($app.MainWindowHandle, [ref]$bounds)) { throw 'Cannot read the installed app window bounds' }
    $width = $bounds.R - $bounds.L
    $height = $bounds.B - $bounds.T
    if ($width -lt 700 -or $height -lt 450) { throw "Unexpected window size ${width}x${height}" }
    $bitmap = [Drawing.Bitmap]::new($width, $height)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $dc = $graphics.GetHdc()
        try {
            if (![AovWindowCapture]::PrintWindow($app.MainWindowHandle, $dc, 2)) { throw 'Windows refused to capture the actual app window' }
        } finally { $graphics.ReleaseHdc($dc) }
        $colors = [System.Collections.Generic.HashSet[int]]::new()
        for ($y=20; $y -lt $height; $y+=10) {
            for ($x=20; $x -lt $width; $x+=10) { [void]$colors.Add($bitmap.GetPixel($x,$y).ToArgb()) }
        }
        if ($colors.Count -lt 24) { throw 'Capture looks blank or incomplete' }
        $bitmap.Save((Join-Path (Resolve-Path artifacts) 'AOV_UABE_2022-2.4-Windows-install-success.png'), [Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
    @(
        "Test host: $([Environment]::OSVersion.VersionString)"
        "Installed EXE: $($exe.Name)"
        "Product version: $($exe.VersionInfo.ProductVersion)"
        "Actual window title: $($app.MainWindowTitle)"
        'Installer exit code: 0'
    ) | Set-Content -Encoding utf8 './artifacts/INSTALL-VERIFICATION.txt'
} finally {
    if (!$app.HasExited) { Stop-Process -Id $app.Id }
}
