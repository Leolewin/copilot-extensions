param(
    [Parameter(Mandatory)]
    [ValidateSet('install', 'update', 'activate', 'status', 'stop', 'uninstall')]
    [string]$Action
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-ActivationPhase(
    [ValidateSet(
        'input', 'input-read', 'principal', 'connect', 'folder', 'lookup', 'validated',
        'run-requested', 'run-returned', 'engine-ready', 'revalidated',
        'process', 'birth', 'response'
    )]
    [string]$Phase
) {
    if ($Action -eq 'activate') { [Console]::Error.WriteLine('broker-phase:' + $Phase) }
}

function Write-NativeFailureCode([Exception]$Failure) {
    for ($depth = 0; $null -ne $Failure -and $depth -lt 8; $depth++) {
        [Console]::Error.WriteLine('broker-native-hresult:' + $Failure.HResult.ToString('X8'))
        $Failure = $Failure.InnerException
    }
}

function Resolve-TaskPrincipalSid([string]$Principal) {
    if ($Principal -match '^S-1-') {
        return ([Security.Principal.SecurityIdentifier]::new($Principal)).Value
    }
    return ([Security.Principal.NTAccount]::new($Principal)).Translate(
        [Security.Principal.SecurityIdentifier]
    ).Value
}

function Find-OwnedTask($Folder, $Config) {
    try { $task = $Folder.GetTask($Config.task_name) }
    catch [System.IO.FileNotFoundException] { return $null }
    catch [Runtime.InteropServices.COMException] {
        $error = $_.Exception
        while ($null -ne $error) {
            if ($error.HResult -eq -2147024894) { return $null }
            $error = $error.InnerException
        }
        throw
    }
    $definition = $task.Definition
    if ($definition.RegistrationInfo.Description -cne $Config.owner) {
        throw 'broker-registration-owner-refused'
    }
    if ((Resolve-TaskPrincipalSid $definition.Principal.UserId) -cne $Config.principal) {
        throw 'broker-registration-principal-refused'
    }
    if ($definition.Principal.LogonType -ne 2 -or $definition.Principal.RunLevel -ne 0 -or
        $definition.Triggers.Count -ne 0 -or $definition.Actions.Count -ne 1 -or
        $definition.Actions.Item(1).Type -ne 0) {
        throw 'broker-registration-policy-refused'
    }
    return $task
}

function Assert-Action($Task, $Config) {
    $definition = $Task.Definition
    $exec = $definition.Actions.Item(1)
    if ($exec.Path -cne $Config.pythonw -or $exec.Arguments -cne $Config.arguments -or
        $exec.WorkingDirectory -cne $Config.cwd -or
        $definition.Settings.MultipleInstances -ne 0 -or
        $definition.Settings.ExecutionTimeLimit -cne 'PT1M' -or
        -not $definition.Settings.AllowDemandStart) {
        throw 'broker-registration-action-refused'
    }
}

function Wait-Drained($Task) {
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    while ($Task.GetInstances(0).Count -ne 0) {
        if ($deadline.Elapsed.TotalSeconds -ge 10) {
            throw 'broker-active-transactions-remain'
        }
        Start-Sleep -Milliseconds 50
    }
}

$rollbackXml = $null
$newRegistration = $false
try {
    Write-ActivationPhase 'input'
    $inputJson = [Console]::In.ReadToEnd()
    Write-ActivationPhase 'input-read'
    $inputValue = $inputJson | ConvertFrom-Json
    $c = $inputValue.config
    Write-ActivationPhase 'principal'
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($sid -cne $c.principal) { throw 'broker-principal-refused' }
    Write-ActivationPhase 'connect'
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    Write-ActivationPhase 'folder'
    $folder = $service.GetFolder('\')
    Write-ActivationPhase 'lookup'
    $task = Find-OwnedTask $folder $c
    if ($Action -eq 'install' -or $Action -eq 'update') {
        if ($null -ne $task) {
            if ($Action -eq 'install') {
                Assert-Action $task $c
            } else {
                # The execution driver owns this mutation; a new slot does not
                # permit stopping or replacing an accepted finite transaction.
                Assert-Action $task $inputValue.previous
                $wasEnabled = $task.Enabled
                $originalXml = $task.Xml
                $task.Enabled = $false
                try {
                    Wait-Drained $task
                } catch {
                    $task.Enabled = $wasEnabled
                    throw
                }
                $rollbackXml = $originalXml
            }
        }
        if ($null -eq $task -or $Action -eq 'update') {
            $definition = $service.NewTask(0)
            $definition.RegistrationInfo.Description = $c.owner
            $definition.Principal.UserId = $c.principal
            $definition.Principal.LogonType = 2
            $definition.Principal.RunLevel = 0
            $definition.Settings.Enabled = $true
            $definition.Settings.AllowDemandStart = $true
            $definition.Settings.Hidden = $true
            $definition.Settings.MultipleInstances = 0
            $definition.Settings.DisallowStartIfOnBatteries = $false
            $definition.Settings.StopIfGoingOnBatteries = $false
            $definition.Settings.ExecutionTimeLimit = 'PT1M'
            $exec = $definition.Actions.Create(0)
            $exec.Path = $c.pythonw
            $exec.Arguments = $c.arguments
            $exec.WorkingDirectory = $c.cwd
            $flags = if ($null -eq $task) { 2 } else { 4 }
            $task = $folder.RegisterTaskDefinition(
                $c.task_name, $definition, $flags, $c.principal, $null, 2, $null
            )
            $newRegistration = $flags -eq 2
            Assert-Action $task $c
        }
    } elseif ($null -eq $task) {
        throw 'broker-adoption-required'
    } else {
        Assert-Action $task $c
    }
    Write-ActivationPhase 'validated'
    if ($Action -eq 'activate') {
        if (-not $task.Enabled) { throw 'broker-admission-disabled' }
        # Only public rendezvous and process identity cross the scheduler.
        # Credentials/argv/environment travel later through the native pipe.
        Write-ActivationPhase 'run-requested'
        $running = $task.RunEx(
            [string[]]@($inputValue.nonce, [string]$inputValue.client_pid, $inputValue.client_birth),
            0, 0, $null
        )
        Write-ActivationPhase 'run-returned'
        $timer = [Diagnostics.Stopwatch]::StartNew()
        do {
            $running.Refresh()
            if ($running.State -eq 4 -and $running.EnginePID -gt 0) { break }
            if ($timer.Elapsed.TotalSeconds -ge 10) { throw 'broker-activation-deadline' }
            Start-Sleep -Milliseconds 25
        } while ($true)
        Write-ActivationPhase 'engine-ready'
        Assert-Action (Find-OwnedTask $folder $c) $c
        Write-ActivationPhase 'revalidated'
        $process = [Diagnostics.Process]::GetProcessById([int]$running.EnginePID)
        Write-ActivationPhase 'process'
        try {
            $birth = $process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString()
        } finally {
            $process.Dispose()
        }
        Write-ActivationPhase 'birth'
        [ordered]@{
            instance = [string]$running.InstanceGuid
            pid = [int]$running.EnginePID
            birth = $birth
            principal = $sid
        } | ConvertTo-Json -Compress
        Write-ActivationPhase 'response'
    } elseif ($Action -eq 'stop' -or $Action -eq 'uninstall') {
        $task.Enabled = $false
        Wait-Drained $task
        if ($Action -eq 'uninstall') { $folder.DeleteTask($c.task_name, 0) }
        [ordered]@{
            phase = 'drained'
            installed = ($Action -ne 'uninstall')
            child_control = 'retained-by-consumer'
        } | ConvertTo-Json -Compress
    } else {
        [ordered]@{
            installed = $true
            configured = $true
            accepting = [bool]$task.Enabled
            active_instances = [int]$task.GetInstances(0).Count
            child_control = 'retained-by-consumer'
        } | ConvertTo-Json -Compress
    }
} catch {
    $failure = $_.Exception.Message
    Write-NativeFailureCode $_.Exception
    try {
        if ($null -ne $rollbackXml) {
            $null = $folder.RegisterTask(
                $c.task_name, $rollbackXml, 4, $c.principal, $null, 2, $null
            )
        } elseif ($newRegistration) {
            $owned = Find-OwnedTask $folder $c
            if ($owned.GetInstances(0).Count -ne 0) {
                throw 'broker-active-transactions-remain'
            }
            $folder.DeleteTask($c.task_name, 0)
        }
    } catch {
        [Console]::Error.WriteLine('broker-registration-rollback-unconfirmed')
        exit 1
    }
    # Never publish COM exception text, a task XML body or captured request data.
    $safeReasons = @(
        'broker-registration-owner-refused', 'broker-registration-action-refused',
        'broker-registration-principal-refused', 'broker-registration-policy-refused',
        'broker-active-transactions-remain', 'broker-principal-refused',
        'broker-adoption-required', 'broker-admission-disabled', 'broker-activation-deadline'
    )
    $reason = if ($failure -cin $safeReasons) {
        $failure
    } else {
        'windows-broker-task-operation-refused'
    }
    [Console]::Error.WriteLine($reason)
    exit 1
}
