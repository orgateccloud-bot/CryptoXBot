# Deploy BotBinance — Oracle Free Tier (ARM)

## Por que Oracle Free Tier?

- **Sempre gratuito**: VM ARM Ampere A1 (4 OCPU, 24 GB RAM)
- **Sem timeout**: diferente de Render/Railway que dormem após inatividade
- **IP fixo**: necessário para restrição de IP na Binance
- **Performance**: ARM é mais eficiente que x86 para cargas contínuas

---

## Passo 1 — Criar conta Oracle Cloud

1. Acesse: https://www.oracle.com/cloud/free/
2. Clique em **Start for free**
3. Preencha os dados (cartão de crédito para verificação, **não cobra**)
4. Confirme o e-mail e aguarde a ativação (~5 minutos)

---

## Passo 2 — Criar a VM (instância ARM)

1. No painel Oracle → **Compute → Instances → Create Instance**
2. Configure:
   - **Nome**: botbinance
   - **Image**: Ubuntu 22.04 (Minimal)
   - **Shape**: `VM.Standard.A1.Flex` → 2 OCPU, 12 GB RAM *(Free Tier)*
   - **Networking**: crie uma VCN padrão, subrede pública
   - **SSH Key**: gere um par ou cole sua chave pública
3. Clique em **Create**
4. Aguarde o status ficar **RUNNING** (~2 minutos)
5. Anote o **IP Público**

---

## Passo 3 — Abrir portas no firewall Oracle

No painel Oracle:
1. **Networking → Virtual Cloud Networks → sua VCN**
2. **Security Lists → Default Security List**
3. **Add Ingress Rules**:

| Protocolo | Porta | Descrição |
|-----------|-------|-----------|
| TCP | 22 | SSH |
| TCP | 80 | Dashboard (HTTP via Nginx) |

> A porta 5000 do Flask fica interna (só o Nginx acessa)

---

## Passo 4 — Abrir porta no firewall Ubuntu

Após conectar ao servidor:
```bash
sudo iptables -I INPUT -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 22  -j ACCEPT
sudo netfilter-persistent save
```

---

## Passo 5 — Conectar ao servidor

```bash
# No Git Bash / WSL da sua máquina:
ssh -i ~/.ssh/oracle_key ubuntu@<IP_DO_SERVIDOR>
```

---

## Passo 6 — Transferir o código

```bash
# Na sua máquina local (Git Bash):
bash deploy/transferir.sh <IP_DO_SERVIDOR>
```

---

## Passo 7 — Instalar no servidor

```bash
# Já no servidor:
cd /opt/botbinance
bash deploy/setup.sh
```

---

## Passo 8 — Configurar chaves da API

```bash
nano config/settings.py
# Cole API_KEY e API_SECRET da Binance
# Salvar: Ctrl+O → Enter → Ctrl+X
```

> **Importante**: atualize a restrição de IP na Binance para o IP do servidor Oracle

---

## Passo 9 — Ativar serviços

```bash
sudo cp deploy/botbinance.service /etc/systemd/system/
sudo cp deploy/dashboard.service  /etc/systemd/system/
sudo systemctl daemon-reload

# Ativar na inicialização
sudo systemctl enable botbinance dashboard

# Iniciar agora
sudo systemctl start botbinance
sudo systemctl start dashboard
```

---

## Passo 10 — Configurar Nginx

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/botbinance
sudo ln -s /etc/nginx/sites-available/botbinance /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx
```

---

## Verificar se está funcionando

```bash
# Status dos serviços
sudo systemctl status botbinance
sudo systemctl status dashboard

# Logs em tempo real
sudo journalctl -u botbinance -f
sudo journalctl -u dashboard -f

# Logs de arquivo
tail -f /opt/botbinance/logs/bot.log
tail -f /opt/botbinance/logs/dashboard.log
```

Dashboard acessível em: `http://<IP_DO_SERVIDOR>`

---

## Atualizar o código depois

```bash
# Na sua máquina local:
bash deploy/transferir.sh <IP_DO_SERVIDOR>

# No servidor:
sudo systemctl restart botbinance dashboard
```

---

## Modo real (quando estiver pronto)

Editar `/etc/systemd/system/botbinance.service`:
```
ExecStart=/opt/botbinance/venv/bin/python main.py --real
```
```bash
sudo systemctl daemon-reload
sudo systemctl restart botbinance
```
