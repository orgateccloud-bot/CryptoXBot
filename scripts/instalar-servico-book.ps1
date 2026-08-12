# Instala o BXBotBook — coletor @bookTicker da pesquisa de microestrutura (E-11)
# Requer elevacao (instalar servico Windows). Espelha a config do BXBotWorker.
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\instalar-servico-book.ps1

$ErrorActionPreference = "Stop"
$py   = "C:\Users\Veloso\AppData\Local\Programs\Python\Python312\python.exe"
$repo = "D:\01_Projetos_Ativos\Geladeira\BinanceXBot"
$nssm = "C:\ProgramData\chocolatey\bin\nssm.exe"

New-Item -ItemType Directory -Force "$repo\logs" | Out-Null

# idempotente: se ja existe, so garante a config e religa
$existe = Get-Service BXBotBook -ErrorAction SilentlyContinue
if (-not $existe) {
    & $nssm install BXBotBook $py "$repo\research\coletar_book.py"
}
& $nssm set BXBotBook AppDirectory $repo
& $nssm set BXBotBook AppStdout "$repo\logs\book_stdout.log"
& $nssm set BXBotBook AppStderr "$repo\logs\book_stderr.log"
& $nssm set BXBotBook AppRotateFiles 1
& $nssm set BXBotBook AppRotateBytes 10485760
& $nssm set BXBotBook AppExit Default Restart
& $nssm set BXBotBook AppRestartDelay 5000
& $nssm set BXBotBook Description "CryptoXbot - coletor @bookTicker (OFI/spread por minuto) para a pesquisa de microestrutura E-11"
& $nssm set BXBotBook Start SERVICE_AUTO_START

& $nssm restart BXBotBook 2>$null
if ($LASTEXITCODE -ne 0) { & $nssm start BXBotBook }

Start-Sleep -Seconds 3
Get-Service BXBotBook | Format-Table Name, Status -AutoSize
