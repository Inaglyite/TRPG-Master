param(
  [string]$ReleaseDirectory = "",
  [string]$BackendBundle = "",
  [switch]$SkipInstallCheck
)

$ErrorActionPreference = "Stop"

function Assert-Condition([bool]$Condition, [string]$Message) {
  if (-not $Condition) {
    throw $Message
  }
}

function Get-FreeTcpPort {
  $Listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Loopback,
    0
  )
  $Listener.Start()
  try {
    return ([System.Net.IPEndPoint]$Listener.LocalEndpoint).Port
  }
  finally {
    $Listener.Stop()
  }
}

function Stop-DesktopLaunch(
  [System.Diagnostics.Process]$Launcher,
  [string]$Marker
) {
  for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
    $ProcessIds = @()
    if ($null -ne $Launcher -and -not $Launcher.HasExited) {
      $ProcessIds += $Launcher.Id
    }
    $ProcessIds += @(
      Get-CimInstance Win32_Process |
        Where-Object {
          $_.CommandLine -and $_.CommandLine.Contains($Marker)
        } |
        Select-Object -ExpandProperty ProcessId
    )
    $ProcessIds = @($ProcessIds | Sort-Object -Unique)
    if ($ProcessIds.Count -eq 0) {
      return
    }
    foreach ($ProcessId in $ProcessIds) {
      if ($ProcessId -and (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
      }
    }
    Start-Sleep -Milliseconds 250
  }
  throw "Could not stop Electron acceptance process tree for marker '$Marker'."
}

function Remove-LaunchDirectory([string]$Path) {
  $LastError = $null
  for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
    if (-not (Test-Path $Path)) {
      return
    }
    try {
      Remove-Item -LiteralPath $Path -Recurse -Force
      return
    }
    catch {
      $LastError = $_
      Start-Sleep -Milliseconds 250
    }
  }
  throw "Could not clean Electron acceptance userData '$Path': $LastError"
}

function Assert-PortableBootstrap([string]$Executable) {
  # The portable target is an extraction wrapper.  On a non-interactive
  # hosted runner it does not reliably forward Chromium's remote-debugging
  # endpoint, so verify its own bootstrap separately and reserve UI probing
  # for the unpacked/installed Electron executable below.
  $Token = "--trpg-portable-probe=$([guid]::NewGuid().ToString('N'))"
  $Probe = Start-Process `
    -FilePath $Executable `
    -WorkingDirectory (Split-Path $Executable -Parent) `
    -ArgumentList "--headless=new --disable-gpu $Token" `
    -PassThru
  try {
    Start-Sleep -Seconds 8
    if ($Probe.HasExited) {
      Assert-Condition ($Probe.ExitCode -eq 0) "Portable bootstrap failed with exit code $($Probe.ExitCode)."
    }
  }
  finally {
    if ($Probe -and -not $Probe.HasExited) {
      & taskkill.exe /PID $Probe.Id /T /F 2>$null | Out-Null
    }
  }
}

function Assert-DesktopLaunch(
  [string]$Executable,
  [string]$Label,
  [string]$DiagnosticsDirectory
) {
  $Token = [guid]::NewGuid().ToString("N")
  $UserData = Join-Path ([IO.Path]::GetTempPath()) "trpg-electron-$Label-$Token"
  $Stdout = Join-Path $DiagnosticsDirectory "$Label.stdout.log"
  $Stderr = Join-Path $DiagnosticsDirectory "$Label.stderr.log"
  $DebugPort = Get-FreeTcpPort
  $Marker = "--trpg-acceptance-token=$Token"
  $Arguments = (
    "--user-data-dir=`"$UserData`" " +
    "--headless=new " +
    "--disable-gpu " +
    "--remote-debugging-address=127.0.0.1 " +
    "--remote-debugging-port=$DebugPort " +
    $Marker
  )
  $Launcher = $null
  try {
    New-Item -ItemType Directory -Path $UserData | Out-Null
    $Launcher = Start-Process `
      -FilePath $Executable `
      -WorkingDirectory (Split-Path $Executable -Parent) `
      -ArgumentList $Arguments `
      -RedirectStandardOutput $Stdout `
      -RedirectStandardError $Stderr `
      -PassThru

    # Portable builds may need to extract the complete Electron runtime on
    # their first launch.  This is especially slow on a clean hosted runner;
    # keep the acceptance deterministic without treating extraction latency as
    # an application failure.
    $Deadline = [DateTime]::UtcNow.AddSeconds(120)
    $ReadyPage = $null
    while ([DateTime]::UtcNow -lt $Deadline) {
      try {
        $Targets = @(
          Invoke-RestMethod `
            -Uri "http://127.0.0.1:$DebugPort/json/list" `
            -Method Get `
            -TimeoutSec 2
        )
        $ReadyPage = $Targets |
          Where-Object {
            $_.type -eq "page" -and
              [string]$_.url -like "file:*"
          } |
          Select-Object -First 1
        if ($null -ne $ReadyPage) {
          break
        }
      }
      catch {
        # The portable wrapper may still be extracting the Electron payload.
      }
      Start-Sleep -Milliseconds 400
    }
    if ($null -eq $ReadyPage) {
      $StderrText = if (Test-Path $Stderr) { Get-Content $Stderr -Raw } else { "" }
      $ProcessSnapshot = @(
        Get-CimInstance Win32_Process |
          Where-Object {
            $_.CommandLine -and (
              $_.CommandLine.Contains($Token) -or
              $_.ExecutablePath -eq $Executable
            )
          } |
          Select-Object ProcessId, Name, ExecutablePath, CommandLine
      ) | ConvertTo-Json -Depth 3 -Compress
      $ProcessSnapshot | Set-Content `
        -Path (Join-Path $DiagnosticsDirectory "$Label.processes.json") `
        -Encoding utf8
      throw "$Label Electron did not expose a ready launcher window within 120 seconds.`n$StderrText`nProcesses: $ProcessSnapshot"
    }
    @{
      label = $Label
      ready = $true
      target_type = $ReadyPage.type
      target_url_scheme = ([uri]$ReadyPage.url).Scheme
    } | ConvertTo-Json | Set-Content `
      -Path (Join-Path $DiagnosticsDirectory "$Label.ready.json") `
      -Encoding utf8
  }
  finally {
    Stop-DesktopLaunch $Launcher $Marker
    Remove-LaunchDirectory $UserData
  }
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ReleaseDirectory) {
  $ReleaseDirectory = Join-Path $ProjectRoot "frontend\release"
}
if (-not $BackendBundle) {
  $BackendBundle = Join-Path $ProjectRoot "release-backend\win\trpg-server"
}
$ReleaseDirectory = (Resolve-Path $ReleaseDirectory).Path
$BackendBundle = (Resolve-Path $BackendBundle).Path
$DiagnosticsDirectory = Join-Path $ReleaseDirectory "acceptance-logs"
New-Item -ItemType Directory -Path $DiagnosticsDirectory -Force | Out-Null

$PreviousExternalBackend = [Environment]::GetEnvironmentVariable(
  "TRPG_EXTERNAL_BACKEND",
  "Process"
)
$PreviousElectronLogging = [Environment]::GetEnvironmentVariable(
  "ELECTRON_ENABLE_LOGGING",
  "Process"
)
$PreviousElectronRunAsNode = [Environment]::GetEnvironmentVariable(
  "ELECTRON_RUN_AS_NODE",
  "Process"
)
$PreviousNodeEnv = [Environment]::GetEnvironmentVariable("NODE_ENV", "Process")
[Environment]::SetEnvironmentVariable("TRPG_EXTERNAL_BACKEND", "1", "Process")
[Environment]::SetEnvironmentVariable("ELECTRON_ENABLE_LOGGING", "1", "Process")
# Some hosted runners/tooling leave this variable in the inherited process
# environment.  An explicit false value is safer than relying on removal:
# Electron must start the packaged app, not Node's argument parser.
[Environment]::SetEnvironmentVariable("ELECTRON_RUN_AS_NODE", "0", "Process")
[Environment]::SetEnvironmentVariable("NODE_ENV", $null, "Process")

try {
  $Installer = @(
    Get-ChildItem -LiteralPath $ReleaseDirectory -Filter "trpg-master-setup-*-x64.exe"
  )
  $Portable = @(
    Get-ChildItem -LiteralPath $ReleaseDirectory -Filter "trpg-master-portable-*-x64.exe"
  )
  Assert-Condition ($Installer.Count -eq 1) "Expected exactly one NSIS installer, found $($Installer.Count)."
  Assert-Condition ($Portable.Count -eq 1) "Expected exactly one portable executable, found $($Portable.Count)."
  Assert-Condition ($Installer[0].Length -gt 10MB) "NSIS installer is unexpectedly small."
  Assert-Condition ($Portable[0].Length -gt 10MB) "Portable executable is unexpectedly small."
  $ChecksumPath = Join-Path $ReleaseDirectory "SHA256SUMS.txt"
  @($Installer[0], $Portable[0]) |
    Sort-Object Name |
    ForEach-Object {
      $Hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
      "$Hash *$($_.Name)"
    } |
    Set-Content -Path $ChecksumPath -Encoding ascii

  $BuiltBackend = Join-Path $BackendBundle "trpg-server.exe"
  $UnpackedDirectory = Join-Path $ReleaseDirectory "win-unpacked"
  $UnpackedBackend = Join-Path $UnpackedDirectory "resources\backend\trpg-server.exe"
  $UnpackedApp = @(
    Get-ChildItem -LiteralPath $UnpackedDirectory -Filter "*.exe" |
      Where-Object { $_.Name -notlike "Uninstall*" }
  )
  Assert-Condition (Test-Path $BuiltBackend -PathType Leaf) "Backend source bundle is missing its executable."
  Assert-Condition (Test-Path $UnpackedBackend -PathType Leaf) "Electron win-unpacked omitted the packaged backend."
  Assert-Condition ($UnpackedApp.Count -ge 1) "Electron win-unpacked omitted the desktop executable."
  Assert-Condition (
    (Get-FileHash $BuiltBackend -Algorithm SHA256).Hash -eq
      (Get-FileHash $UnpackedBackend -Algorithm SHA256).Hash
  ) "Electron win-unpacked contains a different backend executable."

  Assert-PortableBootstrap $Portable[0].FullName

  # The launcher starts on the mode-selection page. TRPG_EXTERNAL_BACKEND=1
  # prevents this acceptance launch from ever spawning/configuring a local
  # server, even if launcher behavior changes unexpectedly. Probe the unpacked
  # executable rather than the portable extraction wrapper because the latter
  # cannot expose a stable remote-debugging endpoint on hosted Windows runners.
  Assert-DesktopLaunch $UnpackedApp[0].FullName "unpacked" $DiagnosticsDirectory

  if (-not $SkipInstallCheck) {
    $InstallRoot = Join-Path (
      [IO.Path]::GetTempPath()
    ) ("trpg-master-nsis-" + [guid]::NewGuid().ToString("N"))
    $Uninstaller = $null
    try {
      # NSIS requires /D to be the final, unquoted command-line parameter. It
      # consumes the remaining text, so a temp path containing spaces is safe.
      # Passing one ArgumentList string preserves that exact ordering.
      $InstallArguments = "/S /D=$InstallRoot"
      $Install = Start-Process `
        -FilePath $Installer[0].FullName `
        -ArgumentList $InstallArguments `
        -PassThru `
        -Wait
      Assert-Condition ($Install.ExitCode -eq 0) "NSIS silent install failed with exit code $($Install.ExitCode)."

      $InstalledBackend = Join-Path $InstallRoot "resources\backend\trpg-server.exe"
      $InstalledApp = @(
        Get-ChildItem -LiteralPath $InstallRoot -Filter "*.exe" |
          Where-Object { $_.Name -notlike "Uninstall*" }
      )
      $Uninstallers = @(
        Get-ChildItem -LiteralPath $InstallRoot -Filter "Uninstall*.exe"
      )
      if ($Uninstallers.Count -gt 0) {
        # Capture this before any content assertion so cleanup still uses the
        # registered uninstaller when a later verification fails.
        $Uninstaller = $Uninstallers[0].FullName
      }
      Assert-Condition (Test-Path $InstalledBackend -PathType Leaf) "NSIS install omitted the packaged backend."
      Assert-Condition ($InstalledApp.Count -ge 1) "NSIS install omitted the desktop executable."
      Assert-Condition ($Uninstallers.Count -eq 1) "NSIS install did not create exactly one uninstaller."
      Assert-Condition (
        (Get-FileHash $BuiltBackend -Algorithm SHA256).Hash -eq
          (Get-FileHash $InstalledBackend -Algorithm SHA256).Hash
      ) "NSIS install contains a different backend executable."

      Assert-DesktopLaunch $InstalledApp[0].FullName "installed" $DiagnosticsDirectory
    }
    finally {
      $UninstallError = $null
      if ($Uninstaller -and (Test-Path $Uninstaller -PathType Leaf)) {
        try {
          $Uninstall = Start-Process `
            -FilePath $Uninstaller `
            -ArgumentList "/S" `
            -PassThru `
            -Wait
          if ($Uninstall.ExitCode -ne 0) {
            throw "NSIS silent uninstall failed with exit code $($Uninstall.ExitCode)."
          }
          $Deadline = [DateTime]::UtcNow.AddSeconds(20)
          while ((Test-Path $InstallRoot) -and [DateTime]::UtcNow -lt $Deadline) {
            Start-Sleep -Milliseconds 250
          }
        }
        catch {
          $UninstallError = $_
        }
      }
      Remove-LaunchDirectory $InstallRoot
      if ($null -ne $UninstallError) {
        throw $UninstallError
      }
    }
  }
}
finally {
  [Environment]::SetEnvironmentVariable(
    "TRPG_EXTERNAL_BACKEND",
    $PreviousExternalBackend,
    "Process"
  )
  [Environment]::SetEnvironmentVariable(
    "ELECTRON_ENABLE_LOGGING",
    $PreviousElectronLogging,
    "Process"
  )
  [Environment]::SetEnvironmentVariable(
    "ELECTRON_RUN_AS_NODE",
    $PreviousElectronRunAsNode,
    "Process"
  )
  [Environment]::SetEnvironmentVariable("NODE_ENV", $PreviousNodeEnv, "Process")
}

Write-Host "Windows desktop artifacts verified." -ForegroundColor Green
