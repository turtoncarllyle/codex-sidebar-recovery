[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CodexHome,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [Parameter(Mandatory = $true)][string]$Pythonw,
    [string]$HostKey,
    [string]$RestartAppId
)

$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This launcher supports Windows only.'
}
$recoveryScript = Join-Path $PSScriptRoot 'recover_sidebar.py'
$pythonPath = (Get-Item -LiteralPath $Pythonw -ErrorAction Stop).FullName
$profilePath = (Get-Item -LiteralPath $CodexHome -ErrorAction Stop).FullName
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)
$recoveryArgs = @($pythonPath, '-B', $recoveryScript, '--codex-home', $profilePath,
    '--output-dir', $outputPath, '--wait')
if ($HostKey) { $recoveryArgs += @('--host-key', $HostKey) }
if ($RestartAppId) { $recoveryArgs += @('--restart-app-id', $RestartAppId) }
foreach ($value in $recoveryArgs) {
    if ($value -match '["\r\n]' -or $value.EndsWith('\')) {
        throw 'Arguments must not contain quotes, line breaks, or trailing backslashes.'
    }
}
$commandLine = ($recoveryArgs | ForEach-Object { '"' + $_ + '"' }) -join ' '
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }

# WMI owns the helper so closing Codex does not terminate its descendant processes.
$created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $commandLine
    CurrentDirectory = $PSScriptRoot
    ProcessStartupInformation = $startup
}
if ($created.ReturnValue -ne 0) { throw "WMI process creation failed: $($created.ReturnValue)" }
$worker = Get-CimInstance Win32_Process -Filter "ProcessId=$($created.ProcessId)"
$owner = if ($worker) { Get-CimInstance Win32_Process -Filter "ProcessId=$($worker.ParentProcessId)" }
[PSCustomObject]@{
    HelperPid = $created.ProcessId
    ParentName = $owner.Name
    StatusPath = Join-Path $outputPath 'status.json'
    Note = 'Verify waiting_for_exit and the helper parent before exiting Codex.'
} | ConvertTo-Json
