# Simplified QuickBooks installer for Windows (PowerShell).
#
#   irm https://raw.githubusercontent.com/andrewliang13/simplified_quickbooks/main/install.ps1 | iex
#
# Installs (or updates) the app in %USERPROFILE%\SimplifiedQuickBooks, adds a Desktop shortcut and starts it.
# Updating never touches your books (books.db) or the backups folder.
# Environment: SQB_HOME (install folder), SQB_ZIP_URL (download location of the app's zip),
#              SQB_NO_START=1 (do not open the app), SQB_TEST=1 (run the test suite after installing).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$zipUrl = if ($env:SQB_ZIP_URL) { $env:SQB_ZIP_URL } else { 'https://github.com/andrewliang13/simplified_quickbooks/archive/refs/heads/main.zip' }
$dest = if ($env:SQB_HOME) { $env:SQB_HOME } else { Join-Path $HOME 'SimplifiedQuickBooks' }

function Find-Python {
    # Returns a command line such as 'py -3' or 'python', or $null.
    foreach ($cmd in @('py -3', 'python', 'python3')) {
        $parts = $cmd -split ' '
        $rest = @($parts | Select-Object -Skip 1)
        try {
            & $parts[0] @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return $cmd }
        } catch { }
    }
    return $null
}

# 1. Python 3.9 or newer
$py = Find-Python
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host 'Python 3.9 or newer was not found. Installing Python 3.12 with winget...'
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
        $py = Find-Python
    }
    if (-not $py) {
        Write-Host 'Please install Python from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this installer again.'
        return
    }
}
$pyParts = $py -split ' '; $pyExe = $pyParts[0]; $pyArgs = @($pyParts | Select-Object -Skip 1)
Write-Host ('Using ' + (& $pyExe @pyArgs --version 2>&1))

# 2. Download and unpack the app (code only; your data files are never in the download)
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('sqb_' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Write-Host 'Downloading Simplified QuickBooks...'
    $zip = Join-Path $tmp 'app.zip'
    Invoke-WebRequest -UseBasicParsing -Uri $zipUrl -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x') -Force
    $src = Get-ChildItem (Join-Path $tmp 'x') -Directory | Select-Object -First 1
    if (-not (Test-Path (Join-Path $src.FullName 'run.py'))) { throw 'The download did not contain the app (run.py missing).' }
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Copy-Item -Path (Join-Path $src.FullName '*') -Destination $dest -Recurse -Force
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
Write-Host "Installed in $dest"

# 3. Desktop shortcut
$desktop = [Environment]::GetFolderPath('Desktop')
if ($desktop) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut((Join-Path $desktop 'Simplified QuickBooks.lnk'))
    $lnk.TargetPath = Join-Path $dest 'start_windows.bat'
    $lnk.WorkingDirectory = $dest
    $lnk.Save()
    Write-Host "Added 'Simplified QuickBooks' to your Desktop. Double-click it to start the app."
}

# 4. Optional self-test
if ($env:SQB_TEST -eq '1') {
    Write-Host 'Running the test suite (about a minute)...'
    Push-Location $dest; & $pyExe @pyArgs -m unittest discover -s tests; Pop-Location
}

# 5. Start
if ($env:SQB_NO_START -ne '1') {
    Write-Host 'Starting the app in a new window. Keep that window open while you work.'
    Start-Process -FilePath (Join-Path $dest 'start_windows.bat') -WorkingDirectory $dest
}
