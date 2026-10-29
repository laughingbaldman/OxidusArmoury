$ErrorActionPreference='Continue'
Write-Host "=== Oxidus setup (Oxidus is working here) ===" -ForegroundColor Cyan
$root='C:\Users\Baldman\OxidusArmoury'
$pyw="$root\.venv\Scripts\pythonw.exe"
$ico=(Get-ChildItem $root -Recurse -Include *.ico -ErrorAction SilentlyContinue | Where-Object FullName -notmatch '\\.venv\\|node_modules' | Select-Object -First 1).FullName
$ws=New-Object -ComObject WScript.Shell
foreach($p in @("$([Environment]::GetFolderPath('Desktop'))\Oxidus.lnk","$([Environment]::GetFolderPath('Startup'))\Oxidus.lnk")){
  $s=$ws.CreateShortcut($p); $s.TargetPath=$pyw; $s.Arguments="`"$root\OxidusApp.pyw`""; $s.WorkingDirectory=$root
  if($ico){$s.IconLocation=$ico}; $s.Save(); Write-Host "Shortcut set: $p" -ForegroundColor Green }
Write-Host "Starting Oxidus server (first start can take a minute)..." -ForegroundColor Yellow
Start-Process $pyw "`"$root\OxidusApp.pyw`"" -WorkingDirectory $root
$ok=$false; for($i=0;$i -lt 180;$i++){ if((Test-NetConnection 127.0.0.1 -Port 5000 -WarningAction SilentlyContinue -InformationLevel Quiet)){$ok=$true;break}; Start-Sleep 1; Write-Host -NoNewline "." }
Write-Host ""
if($ok){Write-Host "Oxidus is UP at http://127.0.0.1:5000 - app window opening." -ForegroundColor Green; "UP" | Set-Content "$root\logs\setup_result.txt"}
else{Write-Host "Server did not come up. Last log lines:" -ForegroundColor Red; Get-Content "$root\logs\oxidus_app_server.log" -Tail 25; "DOWN" | Set-Content "$root\logs\setup_result.txt"}
