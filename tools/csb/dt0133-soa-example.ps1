# Uses the CSB SOA SDK assembly. Lookup is read-only.
# Generate runs the original MERP command for the entire supplied buffer.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Server,
    [ValidateRange(1,65535)][int]$Port = 61105,
    [Parameter(Mandatory)][string]$SdkPath,
    [ValidateSet('Lookup','Insert','Generate','Submit')][string]$Action = 'Lookup',
    [Parameter(Mandatory)][ValidatePattern('^[0-9]+$')][string]$Center,
    [Parameter(Mandatory)][ValidatePattern('^[0-9]{8}$')][string]$PlanDate,
    [Parameter(Mandatory)][ValidatePattern('^[0-9]+$')][string]$Item,
    [Parameter(Mandatory)][ValidatePattern('^[0-9]{1,5}$')][string]$Promotion,
    [ValidatePattern('^[0-9]+$')][string]$Variant,
    [string]$WeightKg,
    [ValidatePattern('^[0-9]+$')][string]$Customer,
    [ValidatePattern('^[0-9]+$')][string]$Priority,
    [ValidatePattern('^[0-9]+$')][string]$Contract
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'assert-dt0133-order-input.ps1')
$culture = [Globalization.CultureInfo]::InvariantCulture
[void][datetime]::ParseExact($PlanDate, 'yyyyMMdd', $culture)
[void][Reflection.Assembly]::LoadFrom((Resolve-Path -LiteralPath $SdkPath).Path)
$Client = New-Object CSB.SOA.API.MerpApi($Server, $Port)
$selection = 'T1+{0}:T2+{1}:T4+{2}' -f $Center, $PlanDate, $Item
$lookup = 'DT0133+PROD-ORDER;?:F3+1:{0}:F551+100:F810+"T1,T2,T4,T6,T7,T24,T25,T31,T34,T63"' -f $selection
$writeAttempted = $false

function Invoke-CsbLine([string]$Line) {
    $transport = $Client.SendLine($Line, $true)
    $status = $Client.GetMerpStatus()
    $rows = @($Client.GetResultSet() | Where-Object { $_ })
    if ($transport -ne 1 -or $status -notmatch ':L2\+0\+0\+0:') {
        throw "CSB did not confirm the command (transport $transport, status $status). Check Lookup before retrying a write."
    }
    if ($Line -eq $lookup -and $rows.Count -ge 100) {
        throw 'Lookup reached its limit. A unique match cannot be verified.'
    }
    $rows
}

function Find-Promotion([string[]]$Rows) {
    foreach ($row in $Rows) {
        $tag = [regex]::Match($row, ':T63\+([0-9]+)(?=:|$)')
        if ($tag.Success -and [int]$tag.Groups[1].Value -eq [int]$Promotion) {
            foreach ($field in @("T1+$Center", "T2+$PlanDate", "T4+$Item")) {
                if ($field -notin ($row.Split(':'))) { throw 'CSB returned a different center, date or item.' }
            }
            $row
        }
    }
}

try {
    $Client.SetTimeOut(320000)
    $Client.SetReceiptTimeout(320000)
    $scopedRows = @(Invoke-CsbLine $lookup)
    $matchesByPromotion = @(Find-Promotion $scopedRows)
    if ($matchesByPromotion.Count -gt 1) { throw 'Multiple orders match the requested T63 in this scope.' }

    if ($Action -eq 'Insert' -or ($Action -eq 'Submit' -and $matchesByPromotion.Count -eq 0)) {
        if ($scopedRows.Count -ne 0) { throw 'An order already exists for this center, date and item. Insert skipped.' }
        if (-not $Customer) { throw 'Insert requires Customer.' }
        Assert-Dt0133OrderInput -Client $Client -Item $Item -Variant $Variant -Center $Center -Quantity $WeightKg | Write-Verbose
        $line = 'DT0133+PROD-ORDER:L7+1:{0}:T5+{1}:T24+{2}:T34+{3}:T63+{4}:T51+{5}:T87+{6}' -f $selection, $Variant, $WeightKg, $Customer, $Promotion, $Item, $PlanDate
        if ($Priority) { $line += ":T3+$Priority" }
        if ($Contract) { $line += ":T55+$Contract" }
        $writeAttempted = $true
        $null = Invoke-CsbLine ($line + ':F500+INSERT:F7+1')
        $matchesByPromotion = @(Find-Promotion @(Invoke-CsbLine $lookup))
    }
    if ($Action -eq 'Generate' -or ($Action -eq 'Submit' -and $matchesByPromotion.Count -eq 1 -and 'T6+0' -in ($matchesByPromotion[0].Split(':')))) {
        if ($matchesByPromotion.Count -ne 1 -or 'T6+0' -notin ($matchesByPromotion[0].Split(':')) -or 'T31+0' -notin ($matchesByPromotion[0].Split(':'))) {
            throw 'Exactly one pending order with this T63 is required before generating its buffer.'
        }
        Assert-Dt0133OrderInput -Client $Client -Item $Item -Variant $Variant -Center $Center -Quantity $WeightKg | Write-Verbose
        $writeAttempted = $true
        $null = Invoke-CsbLine ('DT0133+PROD-ORDER:L6+{0}:F500+UPDATE:F6+1' -f $Center)
    }
    if ($Action -ne 'Lookup') {
        $matchesByPromotion = @(Find-Promotion @(Invoke-CsbLine $lookup))
    }
    if ($matchesByPromotion.Count -ne 1) { throw "Expected a unique match for T63=$Promotion. Found $($matchesByPromotion.Count)." }

    $row = $matchesByPromotion[0]
    $number = [regex]::Match($row, ':T6\+([0-9]+)(?=:|$)')
    $position = [regex]::Match($row, ':T7\+([0-9]+)(?=:|$)')
    $orderStatus = [regex]::Match($row, ':T31\+([0-9]+)(?=:|$)')
    if (-not $number.Success -or -not $position.Success -or -not $orderStatus.Success) {
        throw 'CSB did not return the order number, position and status fields.'
    }
    $orderNumber = [long]$number.Groups[1].Value
    $orderPosition = [int]$position.Groups[1].Value
    if ($Action -in @('Generate','Submit') -and $orderNumber -eq 0) {
        throw 'CSB accepted the generation command but the order still has no number. Check Lookup before retrying.'
    }
    if ($orderNumber -eq 0) { $orderNumber = $null }
    if ($orderPosition -eq 0) { $orderPosition = $null }
    [pscustomobject]@{
        Promotion = [regex]::Match($row, ':T63\+([0-9]+)(?=:|$)').Groups[1].Value
        OrderNumber = if ($orderNumber) { $number.Groups[1].Value } else { $null }
        Position = $orderPosition
        Status = [int]$orderStatus.Groups[1].Value
        Raw = $row
    }
} catch {
    $_.Exception.Data['CsbRetrySafe'] = (-not $writeAttempted -and $Action -ne 'Lookup')
    throw
} finally {
    $Client.MerpDisConnectNoReconnect()
}
