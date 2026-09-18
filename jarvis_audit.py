#!/usr/bin/env python3
# jarvis_audit.py — Auditoria 360 Jarvis com Notificação Slack & Obsidian
import os
import sys
import urllib.request
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

BRT = timezone(timedelta(hours=-3))
TODAY = datetime.now(BRT).strftime("%Y-%m-%d")
NOW = datetime.now(BRT).strftime("%H:%M:%S")

if sys.platform == "win32":
    PRIMARY_VAULT = Path("C:/Users/marcu/OneDrive/Documentos/Obsidian Vault")
    SECONDARY_VAULT = Path("C:/home/hermes/vault") if Path("C:/home/hermes/vault").exists() else None
else:
    PRIMARY_VAULT = Path("/mnt/c/Users/marcu/OneDrive/Documentos/Obsidian Vault")
    SECONDARY_VAULT = Path("/home/hermes/vault") if Path("/home/hermes/vault").exists() else None
VAULTS = [PRIMARY_VAULT]
if SECONDARY_VAULT and SECONDARY_VAULT != PRIMARY_VAULT:
    VAULTS.append(SECONDARY_VAULT)

OPENJARVIS_DIR = Path("C:/Users/marcu/.openjarvis")
JARVIS_LOG = OPENJARVIS_DIR / "server.log"


def check_databases():
    db_names = [
        "agents.db",
        "approvals.db",
        "audit.db",
        "digest.db",
        "knowledge.db",
        "memory.db",
        "telemetry.db",
        "traces.db",
    ]
    results = {}
    for name in db_names:
        p = OPENJARVIS_DIR / name
        if p.exists():
            try:
                con = sqlite3.connect(str(p))
                cur = con.cursor()
                cur.execute("PRAGMA quick_check;")
                row = cur.fetchone()
                con.close()
                results[name] = row[0] if row else "unknown"
            except Exception as e:
                results[name] = f"error: {e}"
        else:
            results[name] = "missing"
    return results


def check_health():
    try:
        req = urllib.request.Request("http://127.0.0.1:8001/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {"status": "ok", "code": resp.status}
    except Exception as e:
        return {"status": "offline_or_error", "error": str(e)}


def inspect_logs():
    errors = []
    size_mb = 0.0
    if JARVIS_LOG.exists():
        size_mb = JARVIS_LOG.stat().st_size / (1024 * 1024)
        try:
            with open(JARVIS_LOG, "r", encoding="utf-8", errors="ignore") as f:
                for line in f.readlines()[-200:]:
                    if any(w in line for w in ["ERROR", "CRITICAL", "Traceback", "Exception"]):
                        errors.append(line.strip())
        except Exception as e:
            errors.append(str(e))
    return {"size_mb": round(size_mb, 2), "errors_count": len(errors), "sample_errors": errors[:5]}


def main():
    h = check_health()
    dbs = check_databases()
    l = inspect_logs()

    dbs_ok = all(v == "ok" for v in dbs.values() if v != "missing")
    missing_dbs = [k for k, v in dbs.items() if v == "missing"]
    failed_dbs = [k for k, v in dbs.items() if v != "ok" and v != "missing"]

    if h["status"] == "ok" and dbs_ok and l["errors_count"] == 0:
        status_geral = "SAUDÁVEL"
        status_badge = "🟢 **SAUDÁVEL**"
    elif h["status"] != "ok" or failed_dbs:
        status_geral = "CRÍTICO"
        status_badge = "🔴 **CRÍTICO**"
    else:
        status_geral = "ATENÇÃO"
        status_badge = "🟡 **ATENÇÃO**"

    active_issues = []
    future_risks = []

    if h["status"] != "ok":
        active_issues.append(f"API Jarvis offline ou inacessível na porta 8001 ({h.get('error', 'Sem resposta')}).")
    if failed_dbs:
        active_issues.append(f"Falha de integridade em bancos SQLite: {', '.join(failed_dbs)}.")
    if l["errors_count"] > 0:
        future_risks.append(f"{l['errors_count']} exceções ou erros encontrados nas últimas 200 linhas do server.log.")
    if l["size_mb"] > 100:
        future_risks.append(f"Tamanho do log de servidor elevado ({l['size_mb']} MB) — recomendada rotação.")
    if missing_dbs:
        future_risks.append(f"Bancos opcionais ausentes no diretório: {', '.join(missing_dbs)}.")

    db_summary = f"{sum(1 for v in dbs.values() if v == 'ok')}/{len(dbs)} bancos íntegros"
    msg = f"[{NOW}] Auditoria 360 Jarvis: {status_geral} | Health: {h['status']} | DBs: {db_summary} | Log: {l['size_mb']}MB ({l['errors_count']} erros)"
    print(msg)

    # 1. Gerar Relatório de Auditoria 360 no Obsidian
    report_content = f"""# 🛡️ Relatório de Auditoria Diária Completa 360° — OpenJarvis
> **Data**: {TODAY} às {NOW} BRT  
> **Status Geral**: {status_badge}  
> **Alvo**: OpenJarvis Local (Porta 8001, Bancos SQLite, Logs)

## 1. 🚨 Falhas Ativas Identificadas
"""
    if active_issues:
        for issue in active_issues:
            report_content += f"- ❌ {issue}\n"
    else:
        report_content += "- ✅ **Zero falhas ativas!** Servidor local e bancos SQLite 100% operacionais.\n"

    report_content += "\n## 2. 🔮 Riscos Futuros & Alertas Preventivos\n"
    if future_risks:
        for risk in future_risks:
            report_content += f"- ⚠️ {risk}\n"
    else:
        report_content += "- ✅ Nenhum risco crítico iminente detectado.\n"

    report_content += f"""
## 3. 📊 Diagnóstico dos Componentes (Varredura 360°)
| Camada | Diagnóstico Técnico |
| :--- | :--- |
| `health_api` | {'✅ HTTP 200 OK (127.0.0.1:8001)' if h['status'] == 'ok' else '❌ Falha de conexão: ' + str(h.get('error'))} |
| `sqlite_databases` | {db_summary} ({', '.join(f'{k}:{v}' for k, v in list(dbs.items())[:4])}...) |
| `server_log_size` | {l['size_mb']} MB |
| `server_log_errors` | {l['errors_count']} erros recentes identificados |

## 4. 🛠️ Plano de Ação & Sugestões de Correção
"""
    if status_geral == "CRÍTICO":
        report_content += "- 💡 Reiniciar o daemon do OpenJarvis ou verificar se o serviço de background está ativo.\n"
    elif future_risks:
        report_content += "- 💡 Monitorar o crescimento do log e investigar eventuais exceções não impeditivas.\n"
    else:
        report_content += "- 💡 Nenhuma intervenção necessária. Sistema 100% estável.\n"

    report_content += "\n---\n*Auditoria 360° automatizada gerada pelo Hermes OpenJarvis Daily Sentinel.*\n"

    # Salvar em todos os vaults configurados
    for v in VAULTS:
        try:
            auditorias_dir = Path(v) / "Auditorias"
            auditorias_dir.mkdir(parents=True, exist_ok=True)
            report_path = auditorias_dir / f"Jarvis-{TODAY}.md"
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_content)
            print(f"[OBSIDIAN] Relatório salvo: {report_path}")

            d_file = Path(v) / "Diario" / f"{TODAY}.md"
            d_file.parent.mkdir(parents=True, exist_ok=True)
            with open(d_file, "a", encoding="utf-8") as f:
                entry = f"\n- **Varredura Diaria 360 Jarvis (09h00)**: {msg}\n"
                f.write(entry)
        except Exception as e:
            print(f"[OBSIDIAN] Erro ao gravar vault {v}: {e}", file=sys.stderr)

    # 2. Despachar para o Slack
    try:
        report_file_for_slack = str(PRIMARY_VAULT / "Auditorias" / f"Jarvis-{TODAY}.md")
        from slack_report_notifier import send_from_obsidian_markdown
        send_from_obsidian_markdown(report_file_for_slack)
    except Exception as e:
        print(f"[SLACK] Aviso: falha ao enviar relatório ao Slack: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
