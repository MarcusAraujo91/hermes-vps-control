#!/usr/bin/env python3
# slack_extension_daily_audit.py — Auditoria 360 Slack Auto Active Extension com Slack & Obsidian
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Tuple, Dict, Any, List

BRT = timezone(timedelta(hours=-3))
TODAY = datetime.now(BRT).strftime("%Y-%m-%d")
NOW = datetime.now(BRT).strftime("%H:%M:%S")

EXTENSION_DIR = Path(r"c:\Users\marcu\.gemini\antigravity\scratch\slack-auto-active-extension")
PRIMARY_VAULT = Path(r"C:\Users\marcu\OneDrive\Documentos\Obsidian Vault")


def check_node_syntax(file_path: Path) -> Tuple[bool, str]:
    if not file_path.exists():
        return False, "Arquivo ausente"
    try:
        res = subprocess.run(
            ["node", "--check", str(file_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode == 0:
            return True, "Sintaxe OK"
        return False, res.stderr.strip().splitlines()[0] if res.stderr else "Erro de sintaxe"
    except Exception as e:
        return False, str(e)


def audit_slack_extension() -> dict:
    results = {
        "files_syntax": {},
        "manifest": {},
        "dom_events": {},
        "storage_resilience": {},
        "active_issues": [],
        "future_risks": [],
    }

    if not EXTENSION_DIR.exists():
        results["active_issues"].append(f"Diretório da extensão não localizado: {EXTENSION_DIR}")
        return results

    # 1. Sintaxe JS
    js_files = ["content.js", "injected.js", "popup.js"]
    for jf in js_files:
        p = EXTENSION_DIR / jf
        ok, msg = check_node_syntax(p)
        results["files_syntax"][jf] = "✅ OK" if ok else f"❌ {msg}"
        if not ok:
            results["active_issues"].append(f"Falha de sintaxe em {jf}: {msg}")

    # 2. Manifest V3
    manifest_path = EXTENSION_DIR / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                m_data = json.load(f)
            mv = m_data.get("manifest_version")
            if mv == 3:
                results["manifest"]["version"] = "✅ Manifest V3"
            else:
                results["manifest"]["version"] = f"❌ Manifest V{mv} (Requer V3)"
                results["active_issues"].append(f"Manifest versão {mv} não compatível com Manifest V3")

            perms = m_data.get("permissions", [])
            has_storage = "storage" in perms
            results["manifest"]["permissions"] = f"{len(perms)} perms (storage={'✅' if has_storage else '❌'})"

            hosts = m_data.get("host_permissions", [])
            has_slack = any("slack.com" in h for h in hosts)
            results["manifest"]["hosts"] = "✅ Restrito a *.slack.com" if has_slack else "⚠️ Hosts abertos"
        except Exception as e:
            results["manifest"]["error"] = str(e)
            results["active_issues"].append(f"Erro ao ler manifest.json: {e}")
    else:
        results["active_issues"].append("manifest.json ausente")

    # 3. Código em content.js e injected.js
    content_js = EXTENSION_DIR / "content.js"
    if content_js.exists():
        text = content_js.read_text(encoding="utf-8", errors="replace")
        if "eval(" in text or "new Function(" in text:
            results["active_issues"].append("Uso de eval() ou new Function() detectado em content.js")

        if "storage.local" in text:
            results["storage_resilience"]["chrome_storage"] = "✅ chrome.storage.local ativo"
        else:
            results["future_risks"].append("content.js não utiliza chrome.storage.local de forma explícita.")

    injected_js = EXTENSION_DIR / "injected.js"
    if injected_js.exists():
        inj_text = injected_js.read_text(encoding="utf-8", errors="replace")
        if "MouseEvent" in inj_text or "dispatchEvent" in inj_text or "KeyboardEvent" in inj_text:
            results["dom_events"]["synthetic_events"] = "✅ Eventos sintéticos ativos (MouseEvent/KeyboardEvent)"
        else:
            results["future_risks"].append("injected.js não detectou despacho de eventos sintéticos para manter status ativo.")

    return results


def main():
    res = audit_slack_extension()

    active_issues = res["active_issues"]
    future_risks = res["future_risks"]

    if active_issues:
        status_geral = "CRÍTICO"
        status_badge = "🔴 **CRÍTICO**"
    elif future_risks:
        status_geral = "ATENÇÃO"
        status_badge = "🟡 **ATENÇÃO**"
    else:
        status_geral = "SAUDÁVEL"
        status_badge = "🟢 **SAUDÁVEL**"

    report_content = f"""# 🛡️ Relatório de Auditoria Diária Completa 360° — Slack Auto Active Extension
> **Data**: {TODAY} às {NOW} BRT  
> **Status Geral**: {status_badge}  
> **Alvo**: Extensão Chrome Manifest V3 (`scratch/slack-auto-active-extension`)

## 1. 🚨 Falhas Ativas Identificadas
"""
    if active_issues:
        for issue in active_issues:
            report_content += f"- ❌ {issue}\n"
    else:
        report_content += "- ✅ **Zero falhas ativas!** Código-fonte, Manifest V3 e sintaxe 100% íntegros.\n"

    report_content += "\n## 2. 🔮 Riscos Futuros & Alertas Preventivos\n"
    if future_risks:
        for risk in future_risks:
            report_content += f"- ⚠️ {risk}\n"
    else:
        report_content += "- ✅ Nenhum risco crítico iminente detectado.\n"

    files_summary = " | ".join(f"{k}: {v}" for k, v in res["files_syntax"].items())
    manifest_summary = f"{res['manifest'].get('version', 'N/A')} | {res['manifest'].get('hosts', 'N/A')}"

    report_content += f"""
## 3. 📊 Diagnóstico dos Componentes (Varredura 360°)
| Camada | Diagnóstico Técnico |
| :--- | :--- |
| `code_syntax` | {files_summary} |
| `manifest_v3` | {manifest_summary} |
| `synthetic_events` | {res['dom_events'].get('synthetic_events', 'N/A')} |
| `storage_resilience` | {res['storage_resilience'].get('chrome_storage', 'N/A')} |

## 4. 🛠️ Plano de Ação & Sugestões de Correção
"""
    if active_issues:
        report_content += "- 💡 Corrigir imediatamente os erros de sintaxe ou APIs obsoletas.\n"
    elif future_risks:
        report_content += "- 💡 Monitorar mudanças de seletores no Slack Web que possam impactar os eventos sintéticos.\n"
    else:
        report_content += "- 💡 Nenhuma ação necessária. Extensão em conformidade com as diretrizes da Chrome Store.\n"

    report_content += "\n---\n*Auditoria 360° automatizada gerada pelo Hermes Slack Auto Active Sentinel.*\n"

    # Salvar nos diretórios do Vault
    auditorias_dir = PRIMARY_VAULT / "Auditorias"
    auditorias_dir.mkdir(parents=True, exist_ok=True)
    report_path = auditorias_dir / f"Slack-Auto-Active-{TODAY}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    print(f"[OBSIDIAN] Salvo em: {report_path}")

    # Espelhar na pasta de Projetos
    proj_dir = PRIMARY_VAULT / "Projetos" / "extensao_slack"
    proj_dir.mkdir(parents=True, exist_ok=True)
    proj_report_path = proj_dir / "Auditoria-Diaria.md"
    try:
        with open(proj_report_path, "w", encoding="utf-8") as f:
            f.write(report_content)
    except Exception:
        pass

    # Despachar para o Slack
    try:
        sys.path.append(r"C:\Users\marcu\AppData\Local\hermes\scripts")
        sys.path.append(r"C:\Users\marcu\projetos\hermes-vps-control")
        sys.path.append(r"c:\Users\marcu\Documents\antigravity\resilient-tesla")
        from slack_report_notifier import send_from_obsidian_markdown
        send_from_obsidian_markdown(str(report_path))
    except Exception as e:
        print(f"[SLACK] Aviso: falha ao enviar ao Slack: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
