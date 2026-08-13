@echo off
rem Vigia diario da primeira medicao do micro_lab (E-11).
rem Agendado no Task Scheduler como "CryptoXbot Micro Lab" (08:00).
cd /d D:\01_Projetos_Ativos\Geladeira\BinanceXBot
"C:\Users\Veloso\AppData\Local\Programs\Python\Python312\python.exe" research\medir_quando_pronto.py >> logs\micro_vigia.log 2>&1
