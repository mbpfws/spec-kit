#!/usr/bin/env pwsh
# Common PowerShell functions analogous to common.sh

function Get-RepoRoot {
    try {
        $result = git rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -eq 0) {
            return $result
        }
    } catch {
        # Git command failed
    }
    
    # Fall back to script location for non-git repos
    return (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
}

function Get-CurrentBranch {
    # First check if SPECIFY_FEATURE environment variable is set
    if ($env:SPECIFY_FEATURE) {
        return $env:SPECIFY_FEATURE
    }
    
    # Then check git if available
    try {
        $result = git rev-parse --abbrev-ref HEAD 2>$null
        if ($LASTEXITCODE -eq 0) {
            return $result
        }
    } catch {
        # Git command failed
    }
    
    # For non-git repos, try to find the latest feature directory
    $repoRoot = Get-RepoRoot
    $specsDir = Join-Path $repoRoot "specs"
    
    if (Test-Path $specsDir) {
        $latestFeature = ""
        $highest = 0
        
        Get-ChildItem -Path $specsDir -Directory | ForEach-Object {
            if ($_.Name -match '^(\d{3})-') {
                $num = [int]$matches[1]
                if ($num -gt $highest) {
                    $highest = $num
                    $latestFeature = $_.Name
                }
            }
        }
        
        if ($latestFeature) {
            return $latestFeature
        }
    }
    
    # Final fallback
    return "main"
}

function Test-HasGit {
    try {
        git rev-parse --show-toplevel 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Test-FeatureBranch {
    param(
        [string]$Branch,
        [bool]$HasGit = $true
    )
    
    # For non-git repos, we can't enforce branch naming but still provide output
    if (-not $HasGit) {
        Write-Warning "[specify] Warning: Git repository not detected; skipped branch validation"
        return $true
    }
    
    if ($Branch -notmatch '^[0-9]{3}-') {
        Write-Output "ERROR: Not on a feature branch. Current branch: $Branch"
        Write-Output "Feature branches should be named like: 001-feature-name"
        return $false
    }
    return $true
}

function Get-FeatureDir {
    param([string]$RepoRoot, [string]$Branch)
    Join-Path $RepoRoot "specs/$Branch"
}

function Get-ClassificationFile {
    param([string]$RepoRoot)
    if (-not $RepoRoot) {
        $RepoRoot = Get-RepoRoot
    }
    return (Join-Path $RepoRoot ".specify/state/project-classification.json")
}

function Get-ProjectType {
    param([string]$RepoRoot)
    if (-not $RepoRoot) {
        $RepoRoot = Get-RepoRoot
    }
    $classificationFile = Get-ClassificationFile -RepoRoot $RepoRoot
    if (-not (Test-Path $classificationFile)) {
        return "greenfield"
    }

    try {
        $json = Get-Content $classificationFile -Raw | ConvertFrom-Json
        if ($json.project_type) {
            return $json.project_type
        }
    } catch {
        # fall through
    }
    return "greenfield"
}

function Get-FeaturePathsEnv {
    $repoRoot = Get-RepoRoot
    $currentBranch = Get-CurrentBranch
    $hasGit = Test-HasGit
    $featureDir = Get-FeatureDir -RepoRoot $repoRoot -Branch $currentBranch
    $projectType = Get-ProjectType -RepoRoot $repoRoot
    
    [PSCustomObject]@{
        REPO_ROOT     = $repoRoot
        CURRENT_BRANCH = $currentBranch
        HAS_GIT       = $hasGit
        FEATURE_DIR   = $featureDir
        FEATURE_SPEC  = Join-Path $featureDir 'spec.md'
        IMPL_PLAN     = Join-Path $featureDir 'plan.md'
        TASKS         = Join-Path $featureDir 'tasks.md'
        RESEARCH      = Join-Path $featureDir 'research.md'
        DATA_MODEL    = Join-Path $featureDir 'data-model.md'
        QUICKSTART    = Join-Path $featureDir 'quickstart.md'
        CONTRACTS_DIR = Join-Path $featureDir 'contracts'
        PROJECT_TYPE  = $projectType
    }
}

function Test-FileExists {
    param([string]$Path, [string]$Description)
    if (Test-Path -Path $Path -PathType Leaf) {
        Write-Output "  ✓ $Description"
        return $true
    } else {
        Write-Output "  ✗ $Description"
        return $false
    }
}

function Test-DirHasFiles {
    param([string]$Path, [string]$Description)
    if ((Test-Path -Path $Path -PathType Container) -and (Get-ChildItem -Path $Path -ErrorAction SilentlyContinue | Where-Object { -not $_.PSIsContainer } | Select-Object -First 1)) {
        Write-Output "  ✓ $Description"
        return $true
    } else {
        Write-Output "  ✗ $Description"
        return $false
    }
}

# GAID System Functions
function Register-Gaid {
    param(
        [string]$Gaid,
        [string]$Path,
        [string]$Stage,
        [string]$Domain,
        [string]$Dependencies = "",
        [string]$ProjectType = "greenfield"
    )
    
    $repoRoot = Get-RepoRoot
    $registryFile = "$repoRoot\.specify\state\artifact-registry.json"
    $stateDir = Split-Path $registryFile -Parent
    
    if (-not (Test-Path $stateDir)) {
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    }
    
    # Load existing registry
    $entries = @()
    if (Test-Path $registryFile) {
        try {
            $entries = Get-Content $registryFile -Raw | ConvertFrom-Json
            if ($entries -isnot [System.Array]) {
                $entries = @($entries)
            }
        } catch {
            $entries = @()
        }
    }
    
    # Remove existing entry for same path
    $entries = $entries | Where-Object { $_.path -ne $Path }
    
    # Create new entry
    $entry = @{
        gaid = $Gaid
        path = $Path
        stage = $Stage
        domain = $Domain
        dependencies = if ($Dependencies) { $Dependencies.Split(',') } else { @() }
        project_type = $ProjectType
        created_at = (Get-Date).ToString('o')
        checksum = ""
    }
    
    if (Test-Path $Path) {
        $entry.checksum = (Get-FileHash -Path $Path -Algorithm MD5).Hash
    }
    
    $entries += $entry
    
    # Save registry
    $entries | ConvertTo-Json -Depth 10 | Set-Content $registryFile -Encoding UTF8
    
    return $Gaid
}

function Get-NextGaid {
    param([string]$Domain)
    
    $repoRoot = Get-RepoRoot
    $registryFile = "$repoRoot\.specify\state\artifact-registry.json"
    
    $entries = @()
    if (Test-Path $registryFile) {
        try {
            $entries = Get-Content $registryFile -Raw | ConvertFrom-Json
            if ($entries -isnot [System.Array]) {
                $entries = @($entries)
            }
        } catch {
            $entries = @()
        }
    }
    
    # Find highest number for domain
    $highest = 0
    $domainUpper = $Domain.ToUpper()
    foreach ($entry in $entries) {
        if ($entry.gaid -like "GAID-$domainUpper-*") {
            try {
                $num = [int]($entry.gaid.Split('-')[-1])
                if ($num -gt $highest) {
                    $highest = $num
                }
            } catch {
                # ignore parse errors
            }
        }
    }
    
    return "GAID-$domainUpper-{0:D3}" -f ($highest + 1)
}

function Get-GaidContext {
    $repoRoot = Get-RepoRoot
    $registryFile = "$repoRoot\.specify\state\artifact-registry.json"
    
    if (-not (Test-Path $registryFile)) {
        return @{}
    }
    
    try {
        $entries = Get-Content $registryFile -Raw | ConvertFrom-Json
        if ($entries -isnot [System.Array]) {
            $entries = @($entries)
        }
        
        $currentBranch = Get-CurrentBranch
        $featureGaids = @()
        
        foreach ($entry in $entries) {
            if ($entry.path -like "*$currentBranch*") {
                $featureGaids += @{
                    gaid = $entry.gaid
                    domain = $entry.domain
                    stage = $entry.stage
                    dependencies = $entry.dependencies
                }
            }
        }
        
        return @{
            current_branch = $currentBranch
            artifacts = $featureGaids
        }
    } catch {
        return @{}
    }
}
