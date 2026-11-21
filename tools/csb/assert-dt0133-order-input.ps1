function Assert-Dt0133OrderInput {
    param(
        [Parameter(Mandatory=$true)]$Client,
        [string]$Item,
        [string]$Variant,
        [string]$Center,
        [string]$Quantity
    )

    if ($Variant -notmatch '^[0-9]+$') {
        throw "Item $Item needs a numeric BOM variant."
    }
    if ($Quantity -notmatch '^[0-9]+(?:\.[0-9]{1,6})?$' -or
        $Quantity -notmatch '[1-9]') {
        throw "Item $Item needs a positive weight in kilograms with up to 6 decimal places."
    }
    if ($Item -notmatch '^[0-9]+$' -or $Center -notmatch '^[0-9]+$') {
        throw 'Item and production cost center must be numeric.'
    }

    $query = 'DT0061+MAIN;?:T1+{0}:T2+{1}:F810+"T14+,T17+"' -f $Item, $Variant
    $transport = $Client.SendLine($query, $true)
    $status = $Client.GetMerpStatus()
    $rows = @($Client.GetResultSet() | Where-Object { $_ })
    if ($transport -ne 1 -or $status -notmatch ':L2\+0\+0\+0:') {
        throw "CSB could not verify BOM $Item variant $Variant. Status: $status"
    }
    if ($rows.Count -ne 1) {
        throw "Expected one BOM for item $Item variant $Variant; found $($rows.Count)."
    }

    $centerField = [regex]::Match($rows[0], ':T14\+([0-9]+)(?=:|$)')
    $blockedField = [regex]::Match($rows[0], ':T17\+([01])(?=:|$)')
    if (-not $centerField.Success -or -not $blockedField.Success) {
        throw "CSB did not return a valid cost center and block flag for BOM $Item variant $Variant."
    }
    if ($blockedField.Groups[1].Value -eq '1') {
        throw "BOM $Item variant $Variant is blocked in CSB."
    }
    $bomCenter = $centerField.Groups[1].Value
    if ($bomCenter -ne $Center) {
        throw "BOM $Item variant $Variant belongs to cost center $bomCenter; the order specifies $Center."
    }
    "Verified BOM $Item variant ${Variant}: cost center $Center, unblocked, order weight $Quantity kg."
}
