$Host.UI.RawUI.WindowTitle = "v9 training progress"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
Set-Location "E:\电脑涨价分析\tools\agent"
while ($true) {
    Clear-Host
    & "E:\ai2026\Scripts\python.exe" watch_training.py --log train-v9.log
    Write-Host ""
    Write-Host "================================================" -ForegroundColor DarkGray
    Write-Host ("  refreshed {0}   (every 5s)" -f (Get-Date -Format "HH:mm:ss"))
    Write-Host "  closing this window does NOT stop training" -ForegroundColor DarkGray
    Start-Sleep -Seconds 5
}