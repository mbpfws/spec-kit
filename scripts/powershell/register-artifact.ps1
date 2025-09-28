#!/usr/bin/env pwsh

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Gaid,
    
    [Parameter(Mandatory)]
    [string]$Path,
    
    [Parameter(Mandatory)]
    [string]$Stage,
    
    [Parameter(Mandatory)]
    [string]$Domain,
    
    [string]$ProjectType = "",
    [string]$Dependencies = "",
    [string]$Agents = "",
    [hashtable]$Metadata = @{},
    [switch]$DryRun,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

function Show-Usage {
    @"
Usage: register-artifact.ps1 -Gaid <GAID> -Path <artifact_path> `
    -Stage </init|/constitution|/specify|/clarify|/plan|/tasks|/analyze|/implement|/validate> `
    -Domain <domain_identifier> [-ProjectType <type>] `
    [-Dependencies dep1,dep2] [-Agents agent1,agent2] [-Metadata @{KEY=VALUE}] [-DryRun]

Registers or updates a GAID entry in .specify/state/artifact-registry.json.

PARAMETERS:
  -Gaid              Required GAID identifier (e.g., GAID-PLN-0001)
  -Path              Relative path to governing artifact (e.g., specs/001-feature/plan.md)
  -Stage             Lifecycle stage (/init,/constitution,/specify,/clarify,/plan,/tasks,/analyze,/implement,/validate)
  -Domain            Architectural/governance domain slug (e.g., planning, research, architecture)
  -ProjectType       Project classification (greenfield, brownfield, ongoing)
  -Dependencies      Comma-separated GAID references this artifact depends on
  -Agents            Comma-separated agent identifiers synchronized with this artifact
  -Metadata          Hashtable of additional KEY=VALUE pairs. Stored under metadata object
  -DryRun            Output resulting JSON to stdout without writing to disk
  -Help              Show this help message

Examples:
  register-artifact.ps1 -Gaid GAID-PLN-0001 -Path specs/001-sample/plan.md `
      -Stage /plan -Domain architecture -ProjectType greenfield

"@
}

function Write-Error-Exit {
    param([string]$Message)
    Write-Error "ERROR: $Message"
    exit 1
}

function Write-Info {
    param([string]$Message)
    Write-Host "[register-artifact] $Message"
}

function Test-CommandExists {
    param([string]$Command)
    $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

if ($Help) {
    Show-Usage
    exit 0
}

if (-not (Test-CommandExists "python3")) {
    Write-Error-Exit "Required command 'python3' not found"
}

function Normalize-Stage {
    param([string]$InputStage)
    switch ($InputStage.ToLower()) {
        { $_ -in @("/init", "init") } { return "/init" }
        { $_ -in @("/constitution", "constitution") } { return "/constitution" }
        { $_ -in @("/specify", "specify") } { return "/specify" }
        { $_ -in @("/clarify", "clarify") } { return "/clarify" }
        { $_ -in @("/plan", "plan") } { return "/plan" }
        { $_ -in @("/tasks", "tasks") } { return "/tasks" }
        { $_ -in @("/analyze", "analyze") } { return "/analyze" }
        { $_ -in @("/implement", "implement") } { return "/implement" }
        { $_ -in @("/validate", "validate") } { return "/validate" }
        default { return $null }
    }
}

$NormalizedStage = Normalize-Stage $Stage
if (-not $NormalizedStage) {
    Write-Error-Exit "Invalid stage '$Stage'"
}
$Stage = $NormalizedStage

# Load common functions
$scriptRoot = Split-Path -Parent $PSCommandPath
$commonScript = Join-Path (Split-Path -Parent $scriptRoot) "powershell\common.ps1"
if (Test-Path $commonScript) {
    . $commonScript
} else {
    Write-Error-Exit "Cannot find common.ps1 at $commonScript"
}

$repoRoot = Get-RepoRoot
$stateDir = Join-Path $repoRoot ".specify\state"
$registryFile = Join-Path $stateDir "artifact-registry.json"

if (-not (Test-Path $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
}

$absPath = Join-Path $repoRoot $Path
if (-not (Test-Path $absPath)) {
    Write-Error-Exit "Artifact path does not exist: $Path (resolved: $absPath)"
}

$normalizedPath = python3 - $Path $repoRoot @'
import os, sys
path, repo = sys.argv[1:]
abs_path = os.path.abspath(os.path.join(repo, path))
relative = os.path.relpath(abs_path, repo)
print(relative.replace("\\", "/"))
'@

function Get-Timestamp {
    return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Parse-CsvLower {
    param([string]$Input)
    if (-not $Input) { return @() }
    return ($Input -split ',' | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
}

$depArray = Parse-CsvLower $Dependencies
$agentArray = Parse-CsvLower $Agents

$ProjectType = $ProjectType.ToLower()
if ($ProjectType -and $ProjectType -notin @("greenfield", "brownfield", "ongoing")) {
    Write-Error-Exit "Invalid project type '$ProjectType'"
}

$metadataJson = if ($Metadata.Count -gt 0) {
    $Metadata | ConvertTo-Json -Compress
} else {
    "{}"
}

if (-not (Test-Path $registryFile)) {
    "[]" | Out-File -FilePath $registryFile -Encoding utf8 -NoNewline
}

try {
    $null = Get-Content $registryFile | ConvertFrom-Json
} catch {
    Write-Error-Exit "Existing registry file contains invalid JSON: $registryFile"
}

$registryContent = Get-Content $registryFile -Raw

$dependencyJson = $depArray | ConvertTo-Json -Compress
$agentJson = $agentArray | ConvertTo-Json -Compress

$checksum = python3 - $absPath @'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], 'rb') as fh:
    for chunk in iter(lambda: fh.read(8192), b''):
        h.update(chunk)
print(h.hexdigest())
'@

$newEntry = @{
    gaid = $Gaid
    path = $normalizedPath
    stage = $Stage
    domain = $Domain
    project_type = if ($ProjectType) { $ProjectType } else { $null }
    dependencies = $depArray
    agents = $agentArray
    metadata = $Metadata
    status = "registered"
    last_synced_at = Get-Timestamp
    checksum = $checksum
} | ConvertTo-Json -Compress

$existingEntries = $registryContent | ConvertFrom-Json
$updatedEntries = @($existingEntries | Where-Object { $_.gaid -ne $Gaid }) + @($newEntry | ConvertFrom-Json)
$updatedJson = $updatedEntries | ConvertTo-Json -Depth 10

if ($DryRun) {
    Write-Output $updatedJson
    Write-Info "Dry run complete; registry not updated"
} else {
    $updatedJson | Out-File -FilePath $registryFile -Encoding utf8 -NoNewline
    Write-Info "Registered $Gaid at $normalizedPath"
}
