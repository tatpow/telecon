#Requires -Version 7
param(
    [Parameter(Mandatory=$true)][string]$Setup,
    [Parameter(Mandatory=$true)][string]$Payload,
    [Parameter(Mandatory=$true)][string]$TestRoot
)
$ErrorActionPreference = 'Stop'
$setupFile = (Resolve-Path -LiteralPath $Setup).Path
$payloadDirectory = (Resolve-Path -LiteralPath $Payload).Path
$testDirectory = [IO.Path]::GetFullPath($TestRoot)
$desktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Телекон.lnk'
$groupDirectory = Join-Path ([Environment]::GetFolderPath('Programs')) 'Телекон'
$registration = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A7F7E82A-1D7B-4F55-8A14-A63F066F746E}_is1'
$existing = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($registration)
if ($null -ne $existing) {
    $existing.Dispose()
    throw 'Installer test refused: a Telecon installation is already registered.'
}
if ((Test-Path -LiteralPath $desktopShortcut) -or (Test-Path -LiteralPath $testDirectory) -or
    (Test-Path -LiteralPath $groupDirectory)) {
    throw 'Installer test refused: a shortcut, Start menu group or test directory already exists.'
}
New-Item -ItemType Directory -Path $testDirectory | Out-Null
$shell = New-Object -ComObject Shell.Application
function Read-Shortcut([string]$Path) {
    # WScript.Shell.CreateShortcut can convert Cyrillic paths through the ANSI
    # codepage. Shell.Application reads the actual Unicode .lnk instead.
    $folder = $shell.Namespace((Split-Path -Parent $Path))
    return $folder.ParseName((Split-Path -Leaf $Path)).GetLink
}
$report = @{ ok = $false; cases = @() }
try {
    foreach ($withShortcuts in @($true, $false)) {
        $label = if ($withShortcuts) { 'with-shortcuts' } else { 'no-shortcuts' }
        $target = Join-Path $testDirectory "$label\Custom Location"
        $menuShortcut = Join-Path $groupDirectory 'Телекон.lnk'
        if (Test-Path -LiteralPath $groupDirectory) { throw 'Test Start menu group already exists' }
        $log = Join-Path $testDirectory "$label-install.log"
        $tasks = if ($withShortcuts) { '/TASKS="startmenuicon,desktopicon"' } else { '/TASKS=""' }
        $installed = $false
        try {
            $process = Start-Process -FilePath $setupFile -ArgumentList @(
                '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/LANG=russian',
                ('/DIR="{0}"' -f $target), $tasks,
                ('/LOG="{0}"' -f $log)
            ) -Wait -PassThru -WindowStyle Hidden
            if ($process.ExitCode -ne 0) { throw "Setup failed: $($process.ExitCode); $log" }
            $installed = $true
            if (-not (Test-Path -LiteralPath (Join-Path $target 'Telecon.exe'))) { throw 'Chosen destination not used' }
            if ([Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $target 'LICENSE'))) -ne
                [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $payloadDirectory 'LICENSE')))) {
                throw 'Installed license differs from the GitHub checkout license'
            }
            foreach ($shortcut in @($desktopShortcut, $menuShortcut)) {
                if ((Test-Path -LiteralPath $shortcut) -ne $withShortcuts) { throw "Shortcut task ignored: $shortcut" }
                if ($withShortcuts) {
                    $link = Read-Shortcut $shortcut
                    if ($link.Path -ne (Join-Path $target 'Telecon.exe') -or $link.WorkingDirectory -ne $target) {
                        throw "Wrong shortcut target or working directory: $shortcut"
                    }
                }
            }
            if ($withShortcuts) {
                $runtimeReport = Join-Path $testDirectory 'installed-runtime.json'
                $app = Start-Process -FilePath (Join-Path $target 'Telecon.exe') -ArgumentList @(
                    '--portable-self-check', ('"{0}"' -f $runtimeReport)
                ) -WorkingDirectory $target -PassThru -WindowStyle Hidden
                if (-not $app.WaitForExit(120000)) { throw 'Installed EXE check timed out' }
                $runtime = Get-Content -LiteralPath $runtimeReport -Raw | ConvertFrom-Json
                if ($app.ExitCode -ne 0 -or -not $runtime.ok) { throw 'Installed EXE did not pass its native check' }
            }
            # A file not created by Setup must survive uninstall, even inside
            # the selected application folder. Never test against real user data.
            [IO.File]::WriteAllText((Join-Path $target 'user-file-kept.txt'), 'synthetic uninstall preservation fixture')
        } finally {
            if ($installed) {
                if (Test-Path -LiteralPath $desktopShortcut) {
                    if ((Read-Shortcut $desktopShortcut).Path -ne (Join-Path $target 'Telecon.exe')) {
                        throw 'Test shortcut was replaced; refusing to uninstall it'
                    }
                }
                $uninstaller = Join-Path $target 'unins000.exe'
                $removal = Start-Process -FilePath $uninstaller -ArgumentList @(
                    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                    ('/LOG="{0}"' -f (Join-Path $testDirectory "$label-uninstall.log"))
                ) -Wait -PassThru -WindowStyle Hidden
                if ($removal.ExitCode -ne 0) { throw "Uninstall failed: $($removal.ExitCode)" }
            }
        }
        if ((Test-Path -LiteralPath $desktopShortcut) -or (Test-Path -LiteralPath $menuShortcut)) {
            throw 'Uninstall left a test shortcut behind'
        }
        if (-not (Test-Path -LiteralPath (Join-Path $target 'user-file-kept.txt'))) { throw 'Uninstall removed an unregistered file' }
        $remaining = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($registration)
        if ($null -ne $remaining) { $remaining.Dispose(); throw 'Uninstall left its registration behind' }
        $report.cases += @{ shortcuts_enabled = $withShortcuts; chosen_directory = $true;
                           license_exact = $true; shortcuts = $true; uninstall = $true;
                           unregistered_file_preserved = $true }
    }
    $report.ok = $true
} catch {
    $report.error = $_.Exception.Message
    throw
} finally {
    $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $testDirectory 'installer-smoke.json') -Encoding utf8
}
$report | ConvertTo-Json -Depth 5
