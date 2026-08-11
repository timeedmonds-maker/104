param(
    [string]$Repo = "timeedmonds-maker/104",
    [Int64]$KeepRunId = 31516744046,
    [int]$Concurrency = 16
)

$ErrorActionPreference = "Stop"

function Ensure-Gh {
    if (Get-Command gh -ErrorAction SilentlyContinue) { return }
    Write-Host "GitHub CLI not found. Installing it with winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI (gh) is not installed and winget is unavailable. Install GitHub CLI from https://cli.github.com/ then rerun this script."
    }
    winget install --id GitHub.cli --exact --accept-source-agreements --accept-package-agreements
    $env:Path += ";$env:ProgramFiles\GitHub CLI"
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI was installed but is not yet on PATH. Close PowerShell, reopen it, and rerun this command."
    }
}

Ensure-Gh

$authOk = $false
try {
    gh auth status 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $authOk = $true }
} catch {}

if (-not $authOk) {
    Write-Host "GitHub authentication is required. A browser login will open now."
    gh auth login --hostname github.com --web --git-protocol https
    if ($LASTEXITCODE -ne 0) { throw "GitHub authentication failed." }
}

$token = (gh auth token).Trim()
if (-not $token) { throw "Could not obtain a GitHub token from gh." }

Write-Host "Repository: $Repo"
Write-Host "PRESERVING production run: $KeepRunId"
Write-Host "Collecting active/queued workflow runs..."

$headers = @{
    Accept = "application/vnd.github+json"
    Authorization = "Bearer $token"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "TREB-queue-cleaner"
}

$statuses = @("queued", "in_progress", "waiting", "requested", "pending")
$runMap = @{}
foreach ($status in $statuses) {
    $page = 1
    do {
        $uri = "https://api.github.com/repos/$Repo/actions/runs?status=$status&per_page=100&page=$page"
        try {
            $resp = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers
        } catch {
            Write-Warning "Could not list status '$status' page $page : $($_.Exception.Message)"
            break
        }
        foreach ($run in @($resp.workflow_runs)) {
            if ([Int64]$run.id -ne $KeepRunId) {
                $runMap[[string]$run.id] = [PSCustomObject]@{
                    id = [Int64]$run.id
                    name = [string]$run.name
                    status = [string]$run.status
                    event = [string]$run.event
                    branch = [string]$run.head_branch
                }
            }
        }
        $count = @($resp.workflow_runs).Count
        $page++
    } while ($count -eq 100)
}

$runs = @($runMap.Values | Sort-Object id)
Write-Host "Found $($runs.Count) stale active/queued runs to cancel."
if ($runs.Count -eq 0) {
    Write-Host "Nothing to cancel."
    exit 0
}

# Reuse one HttpClient and cancel in bounded asynchronous batches. This works in Windows PowerShell 5.1+
Add-Type -AssemblyName System.Net.Http
$handler = New-Object System.Net.Http.HttpClientHandler
$client = New-Object System.Net.Http.HttpClient($handler)
$client.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json")
$client.DefaultRequestHeaders.Add("X-GitHub-Api-Version", "2022-11-28")
$client.DefaultRequestHeaders.UserAgent.ParseAdd("TREB-queue-cleaner")
$client.DefaultRequestHeaders.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", $token)

$cancelled = 0
$failed = New-Object System.Collections.Generic.List[Int64]

for ($i = 0; $i -lt $runs.Count; $i += $Concurrency) {
    $end = [Math]::Min($i + $Concurrency - 1, $runs.Count - 1)
    $batch = @($runs[$i..$end])
    $tasks = @()
    foreach ($run in $batch) {
        $url = "https://api.github.com/repos/$Repo/actions/runs/$($run.id)/cancel"
        $tasks += [PSCustomObject]@{
            Run = $run
            Task = $client.PostAsync($url, $null)
        }
    }

    foreach ($item in $tasks) {
        try {
            $resp = $item.Task.GetAwaiter().GetResult()
            if ($resp.IsSuccessStatusCode -or [int]$resp.StatusCode -eq 409) {
                $cancelled++
            } else {
                # Force-cancel only when ordinary cancellation was rejected.
                $forceUrl = "https://api.github.com/repos/$Repo/actions/runs/$($item.Run.id)/force-cancel"
                $forceResp = $client.PostAsync($forceUrl, $null).GetAwaiter().GetResult()
                if ($forceResp.IsSuccessStatusCode -or [int]$forceResp.StatusCode -eq 409) {
                    $cancelled++
                } else {
                    $failed.Add([Int64]$item.Run.id)
                }
            }
        } catch {
            $failed.Add([Int64]$item.Run.id)
        }
    }

    $done = [Math]::Min($i + $Concurrency, $runs.Count)
    Write-Progress -Activity "Cancelling stale GitHub Actions runs" -Status "$done / $($runs.Count) processed" -PercentComplete (($done / $runs.Count) * 100)
}
Write-Progress -Activity "Cancelling stale GitHub Actions runs" -Completed
$client.Dispose()

Write-Host "Cancellation requests processed: $cancelled"
if ($failed.Count -gt 0) {
    Write-Warning "$($failed.Count) runs could not be cancelled on the first pass. IDs: $($failed -join ', ')"
}

Write-Host "Waiting 5 seconds for GitHub to update queue state..."
Start-Sleep -Seconds 5

$remainingMap = @{}
foreach ($status in $statuses) {
    $page = 1
    do {
        $uri = "https://api.github.com/repos/$Repo/actions/runs?status=$status&per_page=100&page=$page"
        try {
            $resp = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers
        } catch { break }
        foreach ($run in @($resp.workflow_runs)) {
            if ([Int64]$run.id -ne $KeepRunId) { $remainingMap[[string]$run.id] = $true }
        }
        $count = @($resp.workflow_runs).Count
        $page++
    } while ($count -eq 100)
}

Write-Host "Stale active/queued runs remaining: $($remainingMap.Count)"
Write-Host "Preserved production run: $KeepRunId"
Write-Host "DONE"
