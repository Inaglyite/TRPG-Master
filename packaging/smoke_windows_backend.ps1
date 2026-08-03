param(
  [Parameter(Mandatory = $true)]
  [string]$ExpectedMigrationHead,

  [string]$Bundle = "",
  [string]$RuntimeRoot = ""
)

$ErrorActionPreference = "Stop"

function Assert-Condition([bool]$Condition, [string]$Message) {
  if (-not $Condition) {
    throw $Message
  }
}

function Read-JsonEndpoint([string]$Uri) {
  return Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 3
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Bundle) {
  $Bundle = Join-Path $ProjectRoot "release-backend\win\trpg-server"
}
$Bundle = (Resolve-Path $Bundle).Path
$Executable = Join-Path $Bundle "trpg-server.exe"
Assert-Condition (Test-Path $Executable -PathType Leaf) "Packaged backend executable is missing: $Executable"
Assert-Condition (-not [string]::IsNullOrWhiteSpace($ExpectedMigrationHead)) "Expected migration head is empty."

$OwnsRuntimeRoot = [string]::IsNullOrWhiteSpace($RuntimeRoot)
if ($OwnsRuntimeRoot) {
  $RuntimeRoot = Join-Path ([IO.Path]::GetTempPath()) ("trpg-backend-smoke-" + [guid]::NewGuid().ToString("N"))
}
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
if (Test-Path $RuntimeRoot) {
  $existing = @(Get-ChildItem -LiteralPath $RuntimeRoot -Force)
  Assert-Condition ($existing.Count -eq 0) "Smoke runtime must start empty: $RuntimeRoot"
}
else {
  New-Item -ItemType Directory -Path $RuntimeRoot | Out-Null
}

$DatabasePath = Join-Path $RuntimeRoot "trpg-master.db"
$StdoutPath = Join-Path $RuntimeRoot "backend.stdout.log"
$StderrPath = Join-Path $RuntimeRoot "backend.stderr.log"
$ResultPath = Join-Path $RuntimeRoot "smoke-result.json"

$listener = [System.Net.Sockets.TcpListener]::new(
  [System.Net.IPAddress]::Loopback,
  0
)
$listener.Start()
$Port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()
$BaseUri = "http://127.0.0.1:$Port"

$EnvironmentNames = @(
  "TRPG_PROJECT_ROOT",
  "TRPG_RUNTIME_ROOT",
  "TRPG_DATABASE_URL",
  "TRPG_BIND_HOST",
  "TRPG_BIND_PORT",
  "TRPG_REQUIRE_AUTH",
  "TRPG_WRITE_COMPAT_EXPORTS",
  "TRPG_LOG_DIR",
  "PYTHONUTF8"
)
$PreviousEnvironment = @{}
foreach ($Name in $EnvironmentNames) {
  $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

$Process = $null
try {
  [Environment]::SetEnvironmentVariable("TRPG_PROJECT_ROOT", $RuntimeRoot, "Process")
  [Environment]::SetEnvironmentVariable("TRPG_RUNTIME_ROOT", $RuntimeRoot, "Process")
  [Environment]::SetEnvironmentVariable("TRPG_DATABASE_URL", $null, "Process")
  [Environment]::SetEnvironmentVariable("TRPG_BIND_HOST", "127.0.0.1", "Process")
  [Environment]::SetEnvironmentVariable("TRPG_BIND_PORT", [string]$Port, "Process")
  [Environment]::SetEnvironmentVariable("TRPG_REQUIRE_AUTH", "0", "Process")
  [Environment]::SetEnvironmentVariable("TRPG_WRITE_COMPAT_EXPORTS", "0", "Process")
  [Environment]::SetEnvironmentVariable("TRPG_LOG_DIR", (Join-Path $RuntimeRoot "logs"), "Process")
  [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")

  $Process = Start-Process `
    -FilePath $Executable `
    -WorkingDirectory $Bundle `
    -RedirectStandardOutput $StdoutPath `
    -RedirectStandardError $StderrPath `
    -PassThru

  $Deadline = [DateTime]::UtcNow.AddSeconds(60)
  $Health = $null
  while ([DateTime]::UtcNow -lt $Deadline) {
    if ($Process.HasExited) {
      $stderr = if (Test-Path $StderrPath) { Get-Content $StderrPath -Raw } else { "" }
      throw "Packaged backend exited before becoming healthy (exit $($Process.ExitCode)).`n$stderr"
    }
    try {
      $Health = Read-JsonEndpoint "$BaseUri/api/health"
      if ($Health.ok -eq $true) {
        break
      }
    }
    catch {
      Start-Sleep -Milliseconds 400
    }
  }

  Assert-Condition ($null -ne $Health -and $Health.ok -eq $true) "Packaged backend did not become healthy within 60 seconds."
  Assert-Condition ($Health.module -eq "mansion_of_madness") "Health endpoint returned the wrong module."
  Assert-Condition (-not [string]::IsNullOrWhiteSpace([string]$Health.world_id)) "Health endpoint omitted world_id."

  $Ready = Read-JsonEndpoint "$BaseUri/api/ready"
  Assert-Condition ($Ready.ok -eq $true) "Readiness endpoint did not confirm the database round trip."

  $Modules = Read-JsonEndpoint "$BaseUri/api/modules"
  Assert-Condition (@($Modules.modules).Count -gt 0) "Packaged module registry is empty."
  Assert-Condition ($Modules.active -eq "mansion_of_madness") "Packaged module registry has the wrong active module."

  $Characters = Read-JsonEndpoint "$BaseUri/api/characters"
  $CharacterGroups = @($Characters.groups)
  Assert-Condition ($CharacterGroups.Count -gt 0) "Packaged character response omitted groups."
  $CharacterCount = 0
  foreach ($Group in $CharacterGroups) {
    $CharacterCount += @($Group.characters).Count
  }
  Assert-Condition ($CharacterCount -gt 0) "Packaged default character registry is empty."

  $Schema = Read-JsonEndpoint "$BaseUri/api/modules/schema/manifest-v2"
  Assert-Condition ($Schema.type -eq "object") "Packaged module schema endpoint returned an invalid payload."

  Assert-Condition (Test-Path $DatabasePath -PathType Leaf) "Packaged migration hook did not create the SQLite database."
  $DatabaseProbe = @'
import json
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
try:
    revision = connection.execute(
        "SELECT version_num FROM alembic_version"
    ).fetchone()[0]
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
finally:
    connection.close()
print(json.dumps({"revision": revision, "tables": tables}))
'@
  $DatabaseJson = & python -c $DatabaseProbe $DatabasePath
  Assert-Condition ($LASTEXITCODE -eq 0) "Host Python could not inspect the migrated SQLite database."
  $Database = $DatabaseJson | ConvertFrom-Json
  Assert-Condition ($Database.revision -eq $ExpectedMigrationHead) "Packaged database revision '$($Database.revision)' does not match '$ExpectedMigrationHead'."
  foreach ($RequiredTable in @("users", "worlds", "world_investigators", "room_actions")) {
    Assert-Condition ($Database.tables -contains $RequiredTable) "Packaged database is missing table '$RequiredTable'."
  }

  @{
    ok = $true
    migration_head = $Database.revision
    module = $Health.module
    world_id = $Health.world_id
    module_count = @($Modules.modules).Count
    character_count = $CharacterCount
  } | ConvertTo-Json | Set-Content -Path $ResultPath -Encoding utf8

  Write-Host "Packaged backend smoke passed: $BaseUri" -ForegroundColor Green
}
finally {
  if ($null -ne $Process -and -not $Process.HasExited) {
    Stop-Process -Id $Process.Id -Force
    $Process.WaitForExit()
  }
  foreach ($Name in $EnvironmentNames) {
    [Environment]::SetEnvironmentVariable($Name, $PreviousEnvironment[$Name], "Process")
  }
  if ($OwnsRuntimeRoot -and (Test-Path $RuntimeRoot)) {
    Remove-Item -LiteralPath $RuntimeRoot -Recurse -Force
  }
}
