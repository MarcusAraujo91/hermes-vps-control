#!/usr/bin/env python3
"""
slack_report_notifier.py — Centralized Slack Report Dispatcher for Hermes Daily Audits.

Sends formatted, executive Block Kit reports to Marcus's Slack DM via hermes_bot.
Supports:
  1. Direct programmatic dispatch (send_slack_report)
  2. Automatic parsing and dispatch of Obsidian Vault audit markdown files (send_from_obsidian_markdown)
  3. CLI invocation for crons and testing
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Default Slack configuration
DEFAULT_MARCUS_USER_ID = "U0BGUGDKM0V"
DEFAULT_MARCUS_DM_CHANNEL = "D0BJ49PDPV4"
FALLBACK_BOT_TOKEN = ""
TZ_BRT = timezone(timedelta(hours=-3))

SEVERITY_EMOJIS = {
    "OK": "🟢",
    "HEALTHY": "🟢",
    "SAUDÁVEL": "🟢",
    "WARNING": "🟡",
    "ATENÇÃO": "🟡",
    "WARN": "🟡",
    "CRITICAL": "🔴",
    "CRÍTICO": "🔴",
    "ERROR": "🔴",
}


def get_slack_bot_token() -> str:
    """Recupera o token do bot Slack de ambiente, arquivos locais ou fallback."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token and token.startswith("xoxb-"):
        return token.strip()

    candidate_paths = [
        Path(r"C:\Users\marcu\AppData\Local\hermes\scripts\slack_env_tmp.env"),
        Path(os.path.expanduser(r"~\AppData\Local\hermes\scripts\slack_env_tmp.env")),
        Path(r"C:\Users\marcu\AppData\Local\hermes\.env"),
        Path(os.path.expanduser(r"~\AppData\Local\hermes\.env")),
        Path("/home/hermes/.hermes/.env"),
        Path("/home/hermes/.env"),
    ]

    for path in candidate_paths:
        if path.is_file():
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line.startswith("SLACK_BOT_TOKEN=") and not line.startswith("#"):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val.startswith("xoxb-"):
                            return val
            except Exception:
                continue

    return FALLBACK_BOT_TOKEN


def get_status_emoji(status: str) -> str:
    """Retorna o emoji apropriado para a severidade."""
    normalized = status.strip().upper()
    return SEVERITY_EMOJIS.get(normalized, "⚪")


def build_block_kit_report(
    project_name: str,
    status: str,
    summary: str,
    active_issues: Optional[List[str]] = None,
    future_risks: Optional[List[str]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    recommendations: Optional[List[str]] = None,
    vault_file: Optional[str] = None,
    timestamp_str: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Constrói a estrutura Block Kit formatada para o Slack."""
    emoji = get_status_emoji(status)
    status_label = status.strip().upper()
    ts = timestamp_str or datetime.now(TZ_BRT).strftime("%d/%m/%Y %H:%M BRT")

    fallback_text = f"{emoji} [{status_label}] Auditoria 360° — {project_name} ({ts})"

    blocks: List[Dict[str, Any]] = []

    # 1. Header principal
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{emoji} {project_name} — Relatório Diário",
            "emoji": True,
        },
    })

    # 2. Contexto de Execução
    vault_ref = f" | 📁 `{os.path.basename(vault_file)}`" if vault_file else ""
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"📅 *Data:* {ts} | 🎯 *Status Geral:* *{status_label}*{vault_ref}",
            }
        ],
    })

    blocks.append({"type": "divider"})

    # 3. Resumo Executivo
    if summary:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Resumo Executivo:*\n>{summary}",
            },
        })

    # 4. Métricas / Diagnóstico Rápido (em colunas duplas)
    if metrics:
        metric_fields: List[Dict[str, str]] = []
        for key, val in list(metrics.items())[:10]:
            clean_k = key.replace("_", " ").title()
            metric_fields.append({
                "type": "mrkdwn",
                "text": f"*{clean_k}:*\n{val}",
            })
        if metric_fields:
            blocks.append({
                "type": "section",
                "fields": metric_fields,
            })

    # 5. Falhas Ativas
    if active_issues and len(active_issues) > 0:
        issues_text = "\n".join(f"• ❌ {iss.strip()}" for iss in active_issues[:6])
        if len(active_issues) > 6:
            issues_text += f"\n_...e mais {len(active_issues) - 6} falhas._"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"🚨 *Falhas Ativas ({len(active_issues)}):*\n{issues_text}",
            },
        })
    else:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "✅ *Falhas Ativas:* Zero falhas identificadas.",
            },
        })

    # 6. Riscos Futuros & Alertas
    if future_risks and len(future_risks) > 0:
        risks_text = "\n".join(f"• ⚠️ {risk.strip()}" for risk in future_risks[:6])
        if len(future_risks) > 6:
            risks_text += f"\n_...e mais {len(future_risks) - 6} alertas._"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"🔮 *Riscos Futuros & Alertas ({len(future_risks)}):*\n{risks_text}",
            },
        })
    else:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "✅ *Riscos Futuros:* Zero riscos críticos iminentes.",
            },
        })

    # 7. Recomendações
    if recommendations and len(recommendations) > 0:
        rec_text = "\n".join(f"• 💡 {rec.strip()}" for rec in recommendations[:4])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"🛠️ *Plano de Ação Sugerido:*\n{rec_text}",
            },
        })

    blocks.append({"type": "divider"})

    # 8. Rodapé de Origem
    vault_full = f"[[Auditorias/{os.path.basename(vault_file)}]]" if vault_file else "Vault"
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"🤖 *Hermes Bot* • Auditoria Diária Automatizada • Vault: `{vault_full}`",
            }
        ],
    })

    return fallback_text, blocks


def post_to_slack(
    text: str,
    blocks: Optional[List[Dict[str, Any]]] = None,
    channel: str = DEFAULT_MARCUS_DM_CHANNEL,
    token: Optional[str] = None,
    timeout: int = 15,
) -> bool:
    """Envia a mensagem ao Slack via Web API HTTP (chat.postMessage)."""
    bot_token = token or get_slack_bot_token()
    if not bot_token:
        print("[SLACK] ERRO: Token do Slack não configurado.", file=sys.stderr)
        return False

    url = "https://slack.com/api/chat.postMessage"
    payload: Dict[str, Any] = {
        "channel": channel,
        "text": text,
    }
    if blocks:
        payload["blocks"] = blocks

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            res_json = json.loads(raw)
            if res_json.get("ok"):
                ts = res_json.get("ts")
                print(f"[SLACK] Relatório enviado com sucesso para {channel} (ts={ts}).")
                return True
            else:
                err = res_json.get("error", "desconhecido")
                print(f"[SLACK] Falha ao enviar para o Slack: {err}", file=sys.stderr)
                return False
    except urllib.error.URLError as e:
        print(f"[SLACK] Falha de conexão/transporte ao Slack: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[SLACK] Exceção inesperada ao enviar para o Slack: {e}", file=sys.stderr)
        return False


def send_slack_report(
    project_name: str,
    status: str,
    summary: str,
    active_issues: Optional[List[str]] = None,
    future_risks: Optional[List[str]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    recommendations: Optional[List[str]] = None,
    vault_file: Optional[str] = None,
    channel: str = DEFAULT_MARCUS_DM_CHANNEL,
    timestamp_str: Optional[str] = None,
) -> bool:
    """Função pública centralizada para despacho de relatórios de auditoria ao Slack."""
    fallback_text, blocks = build_block_kit_report(
        project_name=project_name,
        status=status,
        summary=summary,
        active_issues=active_issues,
        future_risks=future_risks,
        metrics=metrics,
        recommendations=recommendations,
        vault_file=vault_file,
        timestamp_str=timestamp_str,
    )
    return post_to_slack(text=fallback_text, blocks=blocks, channel=channel)


def parse_obsidian_audit_markdown(file_path: str) -> Dict[str, Any]:
    """Extrai informações estruturadas de um arquivo markdown de auditoria do Obsidian."""
    content = Path(file_path).read_text(encoding="utf-8", errors="replace")

    base_name = Path(file_path).stem
    name_match = re.match(r"^(.*?)-?\d{4}-\d{2}-\d{2}$", base_name)
    project_name = name_match.group(1).replace("-", " ").strip() if name_match else base_name

    first_heading = re.search(r"^#\s+.*?(?:Relatório de Auditoria Diária Completa 360° —|Auditoria Diária:?)\s*(.+)$", content, re.M)
    if first_heading:
        project_name = first_heading.group(1).strip()

    # Normalização dos nomes de projetos
    p_lower = project_name.lower()
    if "resilient-tesla" in p_lower or "hermes vps" in p_lower:
        project_name = "Hermes VPS Control"
    elif "creative-loop-genius" in p_lower or "araujo make" in p_lower:
        project_name = "Araujo Make"
    elif "tv araujo sdr" in p_lower or "sdr" in p_lower:
        project_name = "TV Araujo SDR"
    elif "tv araujo app" in p_lower or "download" in p_lower:
        project_name = "TV Araujo App"
    elif "tv araujo" in p_lower or "tv-araujo" in p_lower:
        project_name = "TV Araujo"
    elif "utm" in p_lower:
        project_name = "UTM.AI"
    else:
        project_name = re.sub(r"^[—–-]\s*", "", project_name).strip()

    # Status geral
    status = "OK"
    status_match = re.search(r"\*\*Status Geral\*\*:\s*([^\n\r]+)", content)
    if status_match:
        raw_status = status_match.group(1).replace("*", "")
        cleaned = re.sub(r"[^\w\sÀ-ÿ]", "", raw_status).strip()
        if cleaned:
            status = cleaned

    # Data e hora
    ts_match = re.search(r"\*\*Data\*\*:\s*([^\n\r]+)", content)
    timestamp_str = ts_match.group(1).strip() if ts_match else None

    # Resumo
    summary = f"Auditoria diária automatizada das 09h00 BRT registrada em [[Auditorias/{Path(file_path).name}]]."

    # Falhas Ativas
    active_issues: List[str] = []
    issues_sec = re.search(r"##\s+(?:1\.\s+)?.*?Falhas Ativas.*?\n(.*?)(?=\n##|\n---|\Z)", content, re.S)
    if issues_sec:
        for line in issues_sec.group(1).splitlines():
            line = line.strip()
            if line.startswith("-") or line.startswith("*"):
                text = re.sub(r"^[-*]\s*", "", line).strip()
                if "Zero falhas" in text or "Nenhuma falha" in text:
                    continue
                clean = re.sub(r"^[❌⚠️✅]\s*", "", text).replace("**", "").strip()
                if clean:
                    active_issues.append(clean)

    # Riscos Futuros
    future_risks: List[str] = []
    risks_sec = re.search(r"##\s+(?:2\.\s+)?.*?Riscos Futuros.*?\n(.*?)(?=\n##|\n---|\Z)", content, re.S)
    if risks_sec:
        for line in risks_sec.group(1).splitlines():
            line = line.strip()
            if line.startswith("-") or line.startswith("*"):
                text = re.sub(r"^[-*]\s*", "", line).strip()
                if "Zero riscos" in text or "Nenhum risco" in text:
                    continue
                clean = re.sub(r"^[❌⚠️✅]\s*", "", text).replace("**", "").strip()
                if clean:
                    future_risks.append(clean)

    # Diagnóstico / Métricas (Tabela Markdown)
    metrics: Dict[str, Any] = {}
    table_sec = re.search(r"##\s+(?:3\.\s+)?.*?(?:Diagnóstico|Componentes|Recursos).*?\n(.*?)(?=\n##|\n---|\Z)", content, re.S)
    if table_sec:
        for line in table_sec.group(1).splitlines():
            line = line.strip()
            if line.startswith("|") and not line.startswith("| :") and not line.startswith("| Camada"):
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2:
                    k = parts[0].replace("`", "").strip()
                    v = parts[1].replace("<br>", " | ")
                    if len(v) > 60:
                        v = v[:57] + "..."
                    metrics[k] = v

    # Recomendações
    recommendations: List[str] = []
    rec_sec = re.search(r"##\s+(?:4\.\s+)?.*?(?:Plano de Ação|Sugestões|Recomendações).*?\n(.*?)(?=\n##|\n---|\Z)", content, re.S)
    if rec_sec:
        for line in rec_sec.group(1).splitlines():
            line = line.strip()
            if line.startswith("-") or line.startswith("*"):
                text = re.sub(r"^[-*]\s*", "", line).strip()
                if "Nenhuma intervenção" in text or text.startswith("*"):
                    continue
                clean = re.sub(r"^[💡*]\s*", "", text).replace("**", "").strip()
                if clean and not clean.startswith("--") and not clean.startswith("Auditoria 360"):
                    recommendations.append(clean)

    return {
        "project_name": project_name,
        "status": status,
        "summary": summary,
        "active_issues": active_issues,
        "future_risks": future_risks,
        "metrics": metrics,
        "recommendations": recommendations,
        "vault_file": str(Path(file_path).resolve()),
        "timestamp_str": timestamp_str,
    }


def send_from_obsidian_markdown(file_path: str, channel: str = DEFAULT_MARCUS_DM_CHANNEL) -> bool:
    """Lê um relatório em markdown salvo no Obsidian Vault e despacha ao Slack."""
    if not os.path.isfile(file_path):
        print(f"[SLACK] Arquivo não encontrado: {file_path}", file=sys.stderr)
        return False

    parsed = parse_obsidian_audit_markdown(file_path)
    return send_slack_report(
        project_name=parsed["project_name"],
        status=parsed["status"],
        summary=parsed["summary"],
        active_issues=parsed["active_issues"],
        future_risks=parsed["future_risks"],
        metrics=parsed["metrics"],
        recommendations=parsed["recommendations"],
        vault_file=parsed["vault_file"],
        channel=channel,
        timestamp_str=parsed["timestamp_str"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Despachante de Relatórios de Auditoria para o Slack")
    parser.add_argument("--file", help="Caminho do arquivo markdown do Obsidian para enviar")
    parser.add_argument("--project", help="Nome do projeto")
    parser.add_argument("--status", default="OK", help="Status: OK, ATENÇÃO, CRÍTICO")
    parser.add_argument("--summary", default="", help="Resumo executivo")
    parser.add_argument("--channel", default=DEFAULT_MARCUS_DM_CHANNEL, help="ID do canal/DM do Slack")
    args = parser.parse_args()

    if args.file:
        ok = send_from_obsidian_markdown(args.file, channel=args.channel)
        return 0 if ok else 1

    if args.project:
        ok = send_slack_report(
            project_name=args.project,
            status=args.status,
            summary=args.summary or f"Auditoria diária manual executada para {args.project}.",
            channel=args.channel,
        )
        return 0 if ok else 1

    print("Uso: slack_report_notifier.py --file <caminho.md> OU --project <nome> [--status OK]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
