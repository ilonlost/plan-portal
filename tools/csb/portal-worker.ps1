[CmdletBinding()]
param(
    [Parameter(Mandatory)][uri]$PortalApi,
    [Parameter(Mandatory)][string]$SdkPath,
    [string]$Server = '10.10.67.34',
    [ValidateRange(1,65535)][int]$Port = 61105,
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
if (-not $env:CSB_TOKEN) { throw 'Set CSB_TOKEN for the portal worker account.' }
if ($PortalApi.Scheme -ne 'https' -and -not ($PortalApi.IsLoopback -and $PortalApi.Scheme -eq 'http')) {
    throw 'Use HTTPS or a local tunnel to connect to the portal API.'
}
$sdk = (Resolve-Path -LiteralPath $SdkPath).Path
$null = [Reflection.Assembly]::LoadFrom($sdk).GetType('CSB.SOA.API.MerpApi', $true)
$url = $PortalApi.AbsoluteUri.TrimEnd('/') + '/integrations/csb/worker'
$headers = @{ Authorization = "Bearer $env:CSB_TOKEN" }
function Invoke-Portal([string]$Route, $Body) {
    $json = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Compress))
    Invoke-RestMethod -Uri "$url/$Route" -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $json -TimeoutSec 30 -MaximumRedirection 0
}

do {
    try {
        $job = (Invoke-Portal 'claim' @{}).job
        if ($null -eq $job) {
            if (-not $Once) { Start-Sleep -Seconds 5 }
            continue
        }
        $params = @{}
        foreach ($name in @('Center','PlanDate','Item','Promotion','Variant','WeightKg','Customer','Priority','Contract')) {
            $params[$name] = [string]$job.parameters.$name
        }
        $receipt = @{ claim = $job.claim }
        try {
            if ([int]$params.Promotion -ne $job.t63 -or $job.action -notin @('Lookup','Submit')) { throw 'Invalid portal job.' }
            $order = & (Join-Path $PSScriptRoot 'dt0133-soa-example.ps1') @params -Server $Server -Port $Port -SdkPath $sdk -Action $job.action
            if (-not $order.OrderNumber) { throw 'The order is still pending in CSB. Check the buffer.' }
            $receipt.returned_t63 = [int]$order.Promotion
            $receipt.order_number = [string]$order.OrderNumber
        } catch {
            $msg = $_.Exception.Message
            $receipt.detail = $msg.Substring(0, [Math]::Min(500, $msg.Length))
            $receipt.retry_safe = $job.action -eq 'Submit' -and $_.Exception.Data['CsbRetrySafe'] -eq $true
        }
        # Retry delivery of the same receipt only. A stale claim is reconciled by Lookup.
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try { $null = Invoke-Portal "$($job.t63)/receipt" $receipt; break }
            catch { if ($attempt -eq 3) { throw }; Start-Sleep -Seconds 3 }
        }
    } catch {
        if ($Once) { throw }
        Write-Warning $_.Exception.Message
        Start-Sleep -Seconds 10
    }
} while (-not $Once)
