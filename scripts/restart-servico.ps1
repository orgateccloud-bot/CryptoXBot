<#
.SYNOPSIS
    Reinicia um servico NSSM do bot (BXBotWorker / BXBotDashboard) e PROVA que
    reiniciou de fato.

.DESCRIPTION
    Existe por causa de duas armadilhas que fazem um restart "bem-sucedido" nao
    reiniciar nada:

    1. Os servicos rodam como LocalSystem, entao controla-los exige ELEVACAO.
       Sem ela, `nssm restart` devolve "Acesso negado" (barulhento, ok), mas
       `Stop-Process -Force` retorna SEM ERRO e nao mata o processo -- falha
       silenciosa. Quem confia nesse "sucesso" acha que subiu codigo novo e
       segue com o processo velho em memoria.
    2. Estar no grupo Administradores NAO basta: com UAC ligado, um shell comum
       recebe token filtrado (o grupo aparece como "usado apenas para negar").
       A janela parece administrativa e nao e.

    Por isso o script (a) se auto-eleva via UAC, (b) aborta se ainda nao estiver
    elevado em vez de falhar em silencio, (c) confirma o restart pelo PID, que e
    a unica prova real, e (d) cai no Stop-Process se o `nssm restart` nao mudar o
    PID -- AppExit Default=Restart faz o NSSM ressuscitar o processo com o codigo
    atual do disco.

    Python le os .py no start: sem PID novo, deploy nenhum entrou em vigor.

.PARAMETER Servico
    Nome do servico. Padrao: BXBotWorker.

.PARAMETER Elevado
    Interno. Marca a instancia relancada com UAC, para a janela nao fechar antes
    de você ler o resultado.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\restart-servico.ps1
    # Dispara o UAC (so consentimento, sem senha), reinicia e mostra a prova.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\restart-servico.ps1 -Servico BXBotDashboard
#>
param(
    [string]$Servico = "BXBotWorker",
    [switch]$Elevado
)

$ErrorActionPreference = "Continue"

function Test-Elevado {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Get-ServicoPid {
    param([string]$Nome)
    $svc = Get-CimInstance Win32_Service -Filter "Name='$Nome'" -ErrorAction SilentlyContinue
    if ($null -eq $svc) { return $null }
    return $svc.ProcessId
}

# ── Auto-elevacao ──────────────────────────────────────────────
if (-not (Test-Elevado)) {
    Write-Host "Nao elevado -- pedindo elevacao via UAC (clique 'Sim')..." -ForegroundColor Yellow
    try {
        Start-Process powershell.exe -Verb RunAs -ErrorAction Stop -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', "`"$PSCommandPath`"",
            '-Servico', $Servico, '-Elevado'
        )
        Write-Host "Janela elevada aberta. O resultado aparece la." -ForegroundColor Green
    } catch {
        Write-Host "UAC recusado ou indisponivel: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Abra 'PowerShell (Administrador)' e rode:" -ForegroundColor Red
        Write-Host "  nssm restart $Servico"
        exit 1
    }
    exit 0
}

# ── Daqui pra baixo: elevado de verdade ────────────────────────
Write-Host "Elevado: True" -ForegroundColor Green

if ($null -eq (Get-ServicoPid -Nome $Servico)) {
    Write-Host "Servico '$Servico' nao existe nesta maquina." -ForegroundColor Red
    if ($Elevado) { Read-Host "`nEnter para fechar" }
    exit 1
}

$pidAntes = Get-ServicoPid -Nome $Servico
Write-Host "PID antes: $pidAntes"

Write-Host "`n-> nssm restart $Servico"
nssm restart $Servico 2>&1 | ForEach-Object { Write-Host "   $_" }

# PID igual = o restart nao pegou. AppExit Default=Restart garante que matar o
# processo o traz de volta com o codigo atual.
Start-Sleep -Seconds 3
if ((Get-ServicoPid -Nome $Servico) -eq $pidAntes) {
    Write-Host "`nPID nao mudou -> matando o processo (NSSM ressuscita)" -ForegroundColor Yellow
    Stop-Process -Id $pidAntes -Force -ErrorAction Continue
}

# Espera voltar com PID NOVO
$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Seconds 2
    $svc = Get-CimInstance Win32_Service -Filter "Name='$Servico'"
} until (($svc.State -eq "Running" -and $svc.ProcessId -ne 0 -and $svc.ProcessId -ne $pidAntes) `
        -or (Get-Date) -gt $deadline)

if ($svc.State -ne "Running") {
    Write-Host "`nServico NAO esta Running (State=$($svc.State)) -- tentando start" -ForegroundColor Red
    nssm start $Servico 2>&1 | ForEach-Object { Write-Host "   $_" }
    Start-Sleep -Seconds 5
    $svc = Get-CimInstance Win32_Service -Filter "Name='$Servico'"
}

# ── Prova ──────────────────────────────────────────────────────
Write-Host "`n================ RESULTADO ================" -ForegroundColor Cyan
Write-Host "Servico:   $Servico"
Write-Host "State:     $($svc.State)"
Write-Host "PID antes: $pidAntes"
Write-Host "PID agora: $($svc.ProcessId)"

$ok = ($svc.State -eq "Running" -and $svc.ProcessId -ne $pidAntes -and $svc.ProcessId -ne 0)
if ($ok) {
    $inicio = (Get-CimInstance Win32_Process -Filter "ProcessId=$($svc.ProcessId)").CreationDate
    Write-Host "=> REINICIOU (processo novo iniciado em $inicio)" -ForegroundColor Green
} else {
    Write-Host "=> NAO reiniciou -- o processo velho segue com o codigo velho" -ForegroundColor Red
}

# Log via nssm (fonte de verdade do redirecionamento, serve para os dois servicos)
$log = (nssm get $Servico AppStdout 2>$null) -replace "`0", ""
if ($log -and (Test-Path $log)) {
    Write-Host "`nUltimas linhas de $log :"
    Get-Content $log -Tail 12 | ForEach-Object { Write-Host "   $_" }
} else {
    Write-Host "`n(sem AppStdout configurado ou log inacessivel)"
}

if ($Elevado) { Read-Host "`nEnter para fechar" }
exit $(if ($ok) { 0 } else { 1 })
