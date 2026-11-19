# SPDX-License-Identifier: Apache-2.0
#
# Installs vts on Windows 10 or 11, with everything it needs:
# mpv, yt-dlp, deno and ffmpeg (through winget, Microsoft's installer),
# Python and pipx, then vts itself. Run again, it updates vts and only
# installs what is missing.
#
# In PowerShell:
#   irm https://raw.githubusercontent.com/ifupauline/vts/main/install.ps1 | iex
# or from a copy of the repository:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# VTS_SOURCE=<path or address> installs vts from there; VTS_NO_WINGET=1
# installs no program (for tests).

function Install-Vts {
    # native programs write warnings on stderr: not errors
    $ErrorActionPreference = 'Continue'

    function Say($text) { Write-Host "vts: $text" -ForegroundColor Cyan }
    function Warn($text) { Write-Host "vts: $text" -ForegroundColor Yellow }

    # what the installers added to the PATH, in this window too
    function Update-Path {
        $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $user = [Environment]::GetEnvironmentVariable('Path', 'User')
        $env:Path = "$machine;$user"
    }

    # where installers put programs without adding them to the PATH
    $places = @{
        'mpv' = @("$env:ProgramFiles\MPV Player", "$env:ProgramFiles\mpv",
                  "$env:LOCALAPPDATA\Programs\mpv")
    }

    function Find-Program($name) {
        if (Get-Command $name -ErrorAction SilentlyContinue) { return $true }
        foreach ($dir in $places[$name]) {
            if ($dir -and (Test-Path (Join-Path $dir "$name.exe"))) {
                return $true
            }
        }
        return $false
    }

    function Get-Package($name, $id) {
        if (Find-Program $name) {
            Say "$name is there"
            return
        }
        if ($env:VTS_NO_WINGET) {
            Warn "$name is missing (not installed: VTS_NO_WINGET)"
            return
        }
        Say "installing $name ($id)..."
        winget install --id $id --exact --source winget --silent `
            --accept-package-agreements --accept-source-agreements | Out-Host
        Update-Path
        if (-not (Find-Program $name)) {
            Warn "$name may need a new window to be found"
        }
    }

    Say 'installing vts and what it needs'

    if (-not $env:VTS_NO_WINGET -and
        -not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host ('vts: winget is missing: install "App Installer" from ' +
                    'the Microsoft Store, then run this again') `
            -ForegroundColor Red
        throw 'winget is missing'
    }

    # --- Python ---------------------------------------------------------
    # a version windows-curses (the screen of the app) is made for
    $versions = @('3.13', '3.14', '3.12', '3.11', '3.10')
    function Find-Python {
        if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
            return $null
        }
        foreach ($v in $versions) {
            $got = & py "-$v" -c 'import sys; print(sys.version_info[:2])'
            if ($LASTEXITCODE -eq 0 -and $got) { return "-$v" }
        }
        return $null
    }
    $py = $null
    $pyArgs = @()
    $found = Find-Python
    if ($found) {
        $py = 'py'
        $pyArgs = @($found)
    }
    if (-not $py) {
        if ($env:VTS_NO_WINGET) {
            throw 'Python 3.10 to 3.14 is missing'
        }
        Say 'installing Python...'
        winget install --id Python.Python.3.13 --exact --source winget `
            --silent --accept-package-agreements `
            --accept-source-agreements | Out-Host
        Update-Path
        $found = Find-Python
        if (-not $found) {
            Write-Host ('vts: Python is installed, but this window does ' +
                        'not see it yet: open a new PowerShell window and ' +
                        'run this again') -ForegroundColor Yellow
            return
        }
        $py = 'py'
        $pyArgs = @($found)
    }
    $version = & $py @pyArgs -c 'import sys; print(sys.version.split()[0])'
    Say "Python $version"

    # --- the programs vts uses -----------------------------------------
    Get-Package 'mpv' 'shinchiro.mpv'
    Get-Package 'yt-dlp' 'yt-dlp.yt-dlp'
    Get-Package 'deno' 'DenoLand.Deno'
    Get-Package 'ffmpeg' 'Gyan.FFmpeg'

    # --- pipx, then vts -------------------------------------------------
    Say 'installing pipx...'
    & $py @pyArgs -m pip install --user --upgrade --quiet `
        --no-warn-script-location pipx | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'pip could not install pipx' }

    $source = 'https://github.com/ifupauline/vts/archive/refs/heads/main.zip'
    if ($env:VTS_SOURCE) {
        $source = $env:VTS_SOURCE
    } elseif ($PSScriptRoot -and
              (Test-Path (Join-Path $PSScriptRoot 'pyproject.toml'))) {
        $source = $PSScriptRoot
    } elseif (Get-Command git -ErrorAction SilentlyContinue) {
        # git can sign in to GitHub, the archive can't
        $source = 'git+https://github.com/ifupauline/vts'
    }
    Say "installing vts from $source..."
    & $py @pyArgs -m pipx install --force $source | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'pipx could not install vts' }

    & $py @pyArgs -m pipx ensurepath | Out-Null
    $bin = & $py @pyArgs -m pipx environment --value PIPX_BIN_DIR
    $bin = "$bin".Trim()
    if (-not $bin) { $bin = "$env:USERPROFILE\.local\bin" }
    $env:Path = "$bin;$env:Path"
    $vts = Join-Path $bin 'vts.exe'
    if (-not (Test-Path $vts)) { throw "vts is not in $bin" }

    # --- does it work? --------------------------------------------------
    Write-Host ''
    & $vts doctor | Out-Host
    Write-Host ''
    Say 'done. Open a new window and type: vts'
    Say ('(vts check --install tells you about new videos of your ' +
         'favourite channels every 30 minutes)')
}

Install-Vts
