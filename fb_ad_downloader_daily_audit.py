#!/usr/bin/env python3
# fb_ad_downloader_daily_audit.py — Auditoria 360 FB Ad Library Extension com Slack & Obsidian
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Tuple, Dict, Any, List

BRT = timezone(timedelta(hours=-3))
TODAY = datetime.now(BRT).strftime("%Y-%m-%d")
NOW = datetime.now(BRT).strftime("%H:%M:%S")

EXTENSION_DIR = Path(r"c:\Users\marcu\.gemini\antigravity\scratch\fb-ad-downloader-extension")
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


def audit_extension() -> dict:
    results = {
        "files_syntax": {},
        "manifest": {},
        "dom_resilience": {},
        "memory_blobs": {},
        "active_issues": [],
        "future_risks": [],
        "recommendations": [],
    }

    if not EXTENSION_DIR.exists():
        results["active_issues"].append(f"Diretório da extensão não localizado: {EXTENSION_DIR}")
        return results

    # 1. Sintaxe JS
    js_files = ["content.js", "zip.js", "popup.js"]
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
            results["manifest"]["permissions"] = f"{len(perms)} permissões ({', '.join(perms)})"

            # Checagem de host_permissions
            hosts = m_data.get("host_permissions", [])
            has_fb = any("facebook.com" in h or "fbcdn.net" in h for h in hosts)
            results["manifest"]["hosts"] = "✅ Escopo Meta/Facebook OK" if has_fb else "⚠️ Hosts genéricos"
        except Exception as e:
            results["manifest"]["error"] = str(e)
            results["active_issues"].append(f"Erro ao parsear manifest.json: {e}")
    else:
        results["active_issues"].append("manifest.json ausente")

    # 3. Análise de código em content.js
    content_js = EXTENSION_DIR / "content.js"
    if content_js.exists():
        text = content_js.read_text(encoding="utf-8", errors="replace")

        # Anti-eval
        if "eval(" in text or "new Function(" in text:
            results["active_issues"].append("Uso perigoso de eval() ou new Function() detectado em content.js")

        # Mutex / flag busy
        if "busy" in text or "isProcessing" in text or "debounce" in text:
            results["dom_resilience"]["mutation_observer"] = "✅ Throttling / Busy flag ativo"
        else:
            results["future_risks"].append("MutationObserver pode sobrecarregar CPU sem flag busy ou debounce.")

        # Revoke Blob
        if "revokeObjectURL" in text:
            results["memory_blobs"]["cleanup"] = "✅ URL.revokeObjectURL presente"
        else:
            results["future_risks"].append("Sem liberação explícita de URL.revokeObjectURL em content.js")

        # TreeWalker
        if "createTreeWalker" in text or "TreeWalker" in text:
            results["dom_resilience"]["treewalker"] = "✅ TreeWalker resiliente ativo"
        else:
            results["dom_resilience"]["treewalker"] = "⚠️ Seletores padrão de DOM"

    return results


def main():
    res = audit_extension()

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

    report_content = f"""# 🛡️ Relatório de Auditoria Diária Completa 360° — FB Ad Library Extension
> **Data**: {TODAY} às {NOW} BRT  
> **Status Geral**: {status_badge}  
> **Alvo**: Extensão Chrome Manifest V3 (`scratch/fb-ad-downloader-extension`)

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
| `dom_tree_walker` | {res['dom_resilience'].get('treewalker', 'N/A')} |
| `mutation_observer` | {res['dom_resilience'].get('mutation_observer', 'N/A')} |
| `memory_cleanup` | {res['memory_blobs'].get('cleanup', 'N/A')} |

## 4. 🛠️ Plano de Ação & Sugestões de Correção
"""
    if active_issues:
        report_content += "- 💡 Corrigir imediatamente os erros de sintaxe ou declaração no código-fonte.\n"
    elif future_risks:
        report_content += "- 💡 Adicionar proteções de throttling ou limpeza de blobs se notar alto consumo de memória.\n"
    else:
        report_content += "- 💡 Nenhuma ação necessária. Extensão em conformidade com Manifest V3.\n"

    report_content += "\n---\n*Auditoria 360° automatizada gerada pelo Hermes FB Ad Downloader Daily Sentinel.*\n"

    # Salvar nos diretórios do Vault
    auditorias_dir = PRIMARY_VAULT / "Auditorias"
    auditorias_dir.mkdir(parents=True, exist_ok=True)
    report_path = auditorias_dir / f"FB-Ad-Downloader-{TODAY}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    print(f"[OBSIDIAN] Salvo em: {report_path}")

    # Espelhar na pasta de Projetos
    proj_dir = PRIMARY_VAULT / "Projetos" / "extensao_biblioteca_anuncios"
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
