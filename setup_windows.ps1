$ProjectDir = $PSScriptRoot
$DockerPath = (Get-Command docker).Source

$action = New-ScheduledTaskAction `
    -Execute $DockerPath `
    -Argument "compose -f `"$ProjectDir\docker-compose.yml`" up -d" `
    -WorkingDirectory $ProjectDir

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "ApprioCOOPlatform" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

Write-Host "Auto-start configured for Windows login."
Write-Host "The platform will start automatically every time you log in."
Write-Host "To open: http://localhost:3000/dashboard"
Write-Host ""
Write-Host "To remove: Unregister-ScheduledTask -TaskName ApprioCOOPlatform -Confirm:`$false"
