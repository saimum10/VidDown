# =============================================================================
# build_windows.ps1 — VidDown (SAIMUM HABIB)
# Builds the Windows version: a PyInstaller bundle, a portable .zip and an
# Inno Setup installer (VidDown_Setup_<version>_x64.exe). This is the Windows
# counterpart of build_deb.sh and is what the GitHub Actions workflow runs.
#
# Local use (PowerShell, Python 3.10+ with Tkinter installed):
#   powershell -ExecutionPolicy Bypass -File .\build_windows.ps1                    # with-js
#   powershell -ExecutionPolicy Bypass -File .\build_windows.ps1 -Variant no-js
#
# Two flavours: "with-js" bundles Deno (the JavaScript runtime YouTube needs,
# together with yt-dlp-ejs); "no-js" is smaller. File names carry the flavour
# and the in-app updater relies on that:
#   VidDown_Setup_<version>_x64_<variant>.exe
#   VidDown_<version>_windows_x64_portable_<variant>.zip
# ($env:DENO_VERSION = "v2.x.y" pins the Deno release; default: latest.)
# =============================================================================
param(
    [ValidateSet("with-js", "no-js")]
    [string]$Variant = "with-js"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"      # makes Invoke-WebRequest much faster

$Root = $PSScriptRoot
Set-Location $Root

$AppName  = "VidDown"
$BuildDir = Join-Path $Root "build"
$BinDir   = Join-Path $Root "bin"
$OutDir   = Join-Path $BuildDir "pyinstaller_out"

function Step($msg) { Write-Host ""; Write-Host "=== $msg ===" -ForegroundColor Cyan }
function Info($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!]  $msg" -ForegroundColor Yellow }

# Runs a native command and stops the script if it fails (PowerShell does not
# do this on its own for external programs).
function Run {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

function Download($url, $dest) {
    for ($i = 1; $i -le 3; $i++) {
        try {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 300
            if ((Get-Item $dest).Length -gt 0) { return }
        } catch {
            Warn "Download attempt $i failed: $url ($($_.Exception.Message))"
            Start-Sleep -Seconds (3 * $i)
        }
    }
    throw "Could not download: $url"
}

foreach ($f in @("LICENSE", "THIRD_PARTY_NOTICES.md")) {
    if (-not (Test-Path (Join-Path $Root $f))) { throw "$f is missing - it ships inside the package." }
}
if (-not (Test-Path (Join-Path $Root "main.py"))) { throw "main.py not found - run this script from inside the VidDown folder." }

# version.py is the single source of truth for the version (same as build_deb.sh)
$Version = (& python -c "import version; print(version.__version__)").Trim()
if (-not $Version) { throw "Could not read version from version.py" }
Write-Host "VidDown $Version - Windows build ($Variant)"

# ---------------------------------------------------------------- step 1 ----
Step "Step 1/6 - Python dependencies"
Run python @("-m", "pip", "install", "--upgrade", "pip")
Run python @("-m", "pip", "install", "-r", "requirements.txt", "pyinstaller")
Run python @("-c", "import tkinter, yt_dlp, PIL; print('tkinter / yt_dlp / Pillow import OK')")

# ---------------------------------------------------------------- step 2 ----
Step "Step 2/6 - ffmpeg / ffprobe"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$FfmpegExe  = Join-Path $BinDir "ffmpeg.exe"
$FfprobeExe = Join-Path $BinDir "ffprobe.exe"

if ((Test-Path $FfmpegExe) -and (Test-Path $FfprobeExe)) {
    Info "ffmpeg.exe / ffprobe.exe already in bin\ - skipping download."
} else {
    $ffName = "ffmpeg-master-latest-win64-gpl.zip"
    $ffBase = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
    $tmp = Join-Path $env:TEMP ("viddown_ff_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp $ffName

    Info "Downloading $ffName ..."
    Download "$ffBase/$ffName" $zip

    # integrity check against the checksum file published next to the archive
    $sums = Join-Path $tmp "checksums.sha256"
    Download "$ffBase/checksums.sha256" $sums
    $line = Get-Content $sums | Where-Object { $_ -match [regex]::Escape($ffName) } | Select-Object -First 1
    if (-not $line) { throw "No checksum entry for $ffName" }
    $expected = ($line -split "\s+")[0].ToLower()
    $actual = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
    if ($expected -ne $actual) { throw "ffmpeg archive checksum mismatch (expected $expected, got $actual)" }
    Info "ffmpeg archive checksum verified."

    Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp "x") -Force
    $f1 = Get-ChildItem -Path (Join-Path $tmp "x") -Recurse -Filter "ffmpeg.exe"  | Select-Object -First 1
    $f2 = Get-ChildItem -Path (Join-Path $tmp "x") -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
    if (-not $f1 -or -not $f2) { throw "ffmpeg.exe / ffprobe.exe not found inside the archive" }
    Copy-Item $f1.FullName $FfmpegExe -Force
    Copy-Item $f2.FullName $FfprobeExe -Force
    Remove-Item $tmp -Recurse -Force
}
& $FfmpegExe -version | Select-Object -First 1

# ---------------------------------------------------------------- step 3 ----
Step "Step 3/6 - aria2c"
$AriaExe = Join-Path $BinDir "aria2c.exe"
if (Test-Path $AriaExe) {
    Info "aria2c.exe already in bin\ - skipping download."
} else {
    try {
        $ariaName = "aria2-1.37.0-win-64bit-build1.zip"
        $ariaUrl  = "https://github.com/aria2/aria2/releases/download/release-1.37.0/$ariaName"
        $tmp = Join-Path $env:TEMP ("viddown_aria_" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        $zip = Join-Path $tmp $ariaName
        Download $ariaUrl $zip
        Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp "x") -Force
        $a = Get-ChildItem -Path (Join-Path $tmp "x") -Recurse -Filter "aria2c.exe" | Select-Object -First 1
        if (-not $a) { throw "aria2c.exe not found inside the archive" }
        Copy-Item $a.FullName $AriaExe -Force
        Remove-Item $tmp -Recurse -Force
    } catch {
        # Same policy as build_deb.sh: no aria2c -> the app uses yt-dlp's own downloader.
        Warn "aria2c could not be prepared ($($_.Exception.Message)). Building without it."
        Remove-Item $AriaExe -Force -ErrorAction SilentlyContinue
    }
}
if (Test-Path $AriaExe) { & $AriaExe --version | Select-Object -First 1 }

# --------------------------------------------------------------- step 3b ----
Step "Step 3b/6 - JavaScript runtime (Deno) [$Variant]"
$DenoExe = Join-Path $BinDir "deno.exe"
if ($Variant -ne "with-js") {
    Info "no-js flavour - Deno is not bundled."
    Remove-Item $DenoExe -Force -ErrorAction SilentlyContinue   # never let a leftover leak into this build
} elseif ((Test-Path $DenoExe) -and ((& $DenoExe --version 2>$null) -match "^deno ")) {
    Info "deno.exe already in bin\ - skipping download."
} else {
    $denoName = "deno-x86_64-pc-windows-msvc.zip"
    $denoVer  = if ($env:DENO_VERSION) { $env:DENO_VERSION } else { "latest" }
    $denoBase = if ($denoVer -eq "latest") { "https://github.com/denoland/deno/releases/latest/download" }
                else { "https://github.com/denoland/deno/releases/download/$denoVer" }
    $tmp = Join-Path $env:TEMP ("viddown_deno_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp $denoName
    Info "Downloading $denoName ($denoVer) ..."
    Download "$denoBase/$denoName" $zip
    # integrity check against the .sha256sum file Deno publishes next to the archive
    $sumFile = Join-Path $tmp "deno.sha256sum"
    Download "$denoBase/$denoName.sha256sum" $sumFile
    # Deno's Windows .sha256sum is PowerShell Get-FileHash output (a "Hash : <hex>" table),
    # the Linux ones are "<hex>  <file>" - so take the first 64-hex-digit token, whatever the layout.
    $m = [regex]::Match((Get-Content -Raw $sumFile), '(?i)\b[0-9a-f]{64}\b')
    $expected = if ($m.Success) { $m.Value.ToLower() } else { "" }
    $actual   = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
    if (-not $expected -or $expected -ne $actual) { throw "Deno archive checksum mismatch (expected $expected, got $actual)" }
    Info "Deno archive checksum verified."
    Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp "x") -Force
    $d = Get-ChildItem -Path (Join-Path $tmp "x") -Recurse -Filter "deno.exe" | Select-Object -First 1
    if (-not $d) { throw "deno.exe not found inside the archive" }
    Copy-Item $d.FullName $DenoExe -Force
    Remove-Item $tmp -Recurse -Force
}
if (Test-Path $DenoExe) { & $DenoExe --version | Select-Object -First 1 }

# ---------------------------------------------------------------- step 4 ----
Step "Step 4/6 - PyInstaller bundle"
$IcoFile = Join-Path $Root "windows\viddown.ico"
if (-not (Test-Path $IcoFile)) {
    Warn "windows\viddown.ico missing - generating it from resources\icon.png"
    Run python @("-c", "from PIL import Image; Image.open(r'resources\icon.png').convert('RGBA').save(r'windows\viddown.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])")
}

Remove-Item -Recurse -Force (Join-Path $BuildDir "pyinstaller_out"), (Join-Path $BuildDir "pyinstaller_work") -ErrorAction SilentlyContinue

# Tells the running app which flavour it is (core/buildinfo.py); the in-app
# updater uses it to fetch the same flavour again.
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
$MarkerFile = Join-Path $BuildDir "build_variant.txt"
Set-Content -Path $MarkerFile -Value $Variant -Encoding ascii

$piArgs = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onedir", "--windowed",
    "--name", $AppName,
    "--icon", $IcoFile,
    "--distpath", $OutDir,
    "--workpath", (Join-Path $BuildDir "pyinstaller_work"),
    "--specpath", $BuildDir,
    "--add-data", "$Root\resources;resources",
    "--add-data", "$Root\browser_extension;browser_extension",
    "--add-binary", "$FfmpegExe;bin",
    "--add-binary", "$FfprobeExe;bin",
    "--add-data", "$MarkerFile;resources",
    "--add-data", "$Root\LICENSE;licenses",
    "--add-data", "$Root\THIRD_PARTY_NOTICES.md;licenses",
    "--exclude-module", "mutagen",
    "--collect-all", "yt_dlp",
    "--collect-all", "yt_dlp_ejs",
    "--hidden-import", "PIL",
    "--hidden-import", "PIL.Image",
    "--hidden-import", "PIL.ImageTk",
    "--hidden-import", "version",
    "--hidden-import", "tkinter",
    "--hidden-import", "tkinter.ttk",
    "--hidden-import", "tkinter.font",
    "--hidden-import", "tkinter.filedialog",
    "--hidden-import", "tkinter.messagebox"
)
if (Test-Path $AriaExe) { $piArgs += @("--add-binary", "$AriaExe;bin") }
# Deno goes in as plain data (copied byte-for-byte, not binary-analysed)
if ($Variant -eq "with-js") { $piArgs += @("--add-data", "$DenoExe;bin") }
$piArgs += "main.py"
Run python $piArgs

$AppDir = Join-Path $OutDir $AppName
if (-not (Test-Path (Join-Path $AppDir "$AppName.exe"))) { throw "$AppName.exe was not produced" }
# Fail the build rather than ship a package without the tools trim/merge need.
foreach ($t in @("ffmpeg.exe", "ffprobe.exe")) {
    $found = Get-ChildItem -Path $AppDir -Recurse -Filter $t | Select-Object -First 1
    if (-not $found) { throw "$t is missing from the bundle" }
}
# License files next to the program too (installer and portable zip are both made from $AppDir)
Copy-Item (Join-Path $Root "LICENSE") $AppDir -Force
Copy-Item (Join-Path $Root "THIRD_PARTY_NOTICES.md") $AppDir -Force

# flavour marker, YouTube solver files and (only for with-js) Deno must be in the bundle
$bundledMarker = Get-ChildItem -Path $AppDir -Recurse -Filter "build_variant.txt" | Select-Object -First 1
if (-not $bundledMarker -or (Get-Content $bundledMarker.FullName -Raw).Trim() -ne $Variant) {
    throw "build_variant.txt is missing from the bundle or has the wrong value"
}
if (-not (Get-ChildItem -Path $AppDir -Recurse -Filter "core.min.js" | Where-Object { $_.FullName -match "yt_dlp_ejs" } | Select-Object -First 1)) {
    throw "yt-dlp-ejs solver files are missing from the bundle (is yt-dlp[default] installed?)"
}
$bundledDeno = Get-ChildItem -Path $AppDir -Recurse -Filter "deno.exe" | Select-Object -First 1
if ($Variant -eq "with-js") {
    if (-not $bundledDeno) { throw "deno.exe is missing from the bundle (with-js flavour)" }
    & $bundledDeno.FullName --version | Select-Object -First 1
    if ($LASTEXITCODE -ne 0) { throw "Bundled deno.exe does not run" }
} elseif ($bundledDeno) {
    throw "no-js flavour but deno.exe ended up in the bundle"
}
$bundledFfmpeg = Get-ChildItem -Path $AppDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
& $bundledFfmpeg.FullName -version | Select-Object -First 1
Info "PyInstaller bundle created: $AppDir"

# ---------------------------------------------------------------- step 5 ----
Step "Step 5/6 - Portable zip"
$PortableZip = Join-Path $Root "VidDown_${Version}_windows_x64_portable_${Variant}.zip"
Remove-Item $PortableZip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $PortableZip -CompressionLevel Optimal
Info "Created $PortableZip"

# ---------------------------------------------------------------- step 6 ----
Step "Step 6/6 - Installer (Inno Setup)"
function Find-Iscc {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, "$env:LOCALAPPDATA\Programs")) {
        if (-not $base) { continue }
        $p = Join-Path $base "Inno Setup 6\ISCC.exe"
        if (Test-Path $p) { return $p }
    }
    return $null
}
$Iscc = Find-Iscc
if (-not $Iscc) {
    Warn "Inno Setup not found - installing it with Chocolatey ..."
    & choco install innosetup -y --no-progress
    $Iscc = Find-Iscc
}
if (-not $Iscc) { throw "Inno Setup (ISCC.exe) is not available. Install it from https://jrsoftware.org/isdl.php and run again." }

Run $Iscc @("/Qp", "/DAppVersion=$Version", "/DVariant=$Variant", "/DSourceDir=$AppDir", "/DOutputDir=$Root", (Join-Path $Root "windows\viddown.iss"))
$Installer = Join-Path $Root "VidDown_Setup_${Version}_x64_${Variant}.exe"
if (-not (Test-Path $Installer)) { throw "Installer was not produced: $Installer" }

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "  Windows build finished ($Variant)" -ForegroundColor Green
Write-Host "==============================================================" -ForegroundColor Green
Info "Installer : $Installer  ($([math]::Round((Get-Item $Installer).Length / 1MB, 1)) MB)"
Info "Portable  : $PortableZip  ($([math]::Round((Get-Item $PortableZip).Length / 1MB, 1)) MB)"
