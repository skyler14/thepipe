param(
    [string]$DistDir = "",
    [string]$BuildRoot = "",
    [switch]$KeepBuild
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "../..")
$LockPath = Join-Path $Root "native/codegraph/upstream.lock"
$Lock = @{}
Get-Content $LockPath | ForEach-Object {
    if ($_ -match "^([^#][^=]+)=(.*)$") {
        $Lock[$Matches[1].Trim()] = $Matches[2].Trim()
    }
}

$UpstreamRepo = $Lock["UPSTREAM_REPO"]
$UpstreamCommit = $Lock["UPSTREAM_COMMIT"]
$UpstreamApiVersion = $Lock["UPSTREAM_API_VERSION"]
$UpstreamBuildTarget = $Lock["UPSTREAM_BUILD_TARGET"]
$ShortCommit = $UpstreamCommit.Substring(0, 12)

if (-not $BuildRoot) {
    $BuildRoot = Join-Path ([System.IO.Path]::GetTempPath()) "thepipe-codegraph-$ShortCommit"
}
if (-not $DistDir) {
    $DistDir = Join-Path $Root "dist/codegraph"
}

$Checkout = Join-Path $BuildRoot "source"
$Stage = Join-Path $BuildRoot "stage"
$ExeName = "codebase-memory-mcp.exe"
$Archive = Join-Path $DistDir "codebase-memory-mcp-windows-amd64-$ShortCommit.zip"

if (Test-Path $BuildRoot) {
    Remove-Item -Recurse -Force $BuildRoot
}
New-Item -ItemType Directory -Force -Path $Checkout, $Stage, $DistDir | Out-Null

try {
    git -C $Checkout init -q
    git -C $Checkout remote add origin $UpstreamRepo
    git -C $Checkout fetch --depth 1 origin $UpstreamCommit
    git -C $Checkout checkout -q FETCH_HEAD

    make -C $Checkout -f Makefile.cbm $UpstreamBuildTarget `
        "CFLAGS_EXTRA=-DCBM_VERSION=`"\`"$UpstreamApiVersion\`"`"" `
        "LIBGIT2_CFLAGS=" "LIBGIT2_LIBS="

    $BuiltExe = Join-Path $Checkout "build/c/codebase-memory-mcp.exe"
    if (-not (Test-Path $BuiltExe)) {
        $BuiltExe = Join-Path $Checkout "build/c/codebase-memory-mcp"
    }
    if (-not (Test-Path $BuiltExe)) {
        throw "Built sidecar executable not found under build/c"
    }

    Copy-Item $BuiltExe (Join-Path $Stage $ExeName)
    if (Test-Path $Archive) {
        Remove-Item -Force $Archive
    }
    Compress-Archive -Path (Join-Path $Stage $ExeName) -DestinationPath $Archive
    $Hash = Get-FileHash -Algorithm SHA256 $Archive
    "$($Hash.Hash.ToLower())  $Archive" | Set-Content -Encoding ascii "$Archive.sha256"
    Write-Host "Built $Archive"
}
finally {
    if (-not $KeepBuild -and (Test-Path $BuildRoot)) {
        Remove-Item -Recurse -Force $BuildRoot
    }
}
