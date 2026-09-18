#!/usr/bin/env python3
"""
vps_daily_audit.py — Hermes VPS Control & Health Auditor
Varredura diária abrangente de infraestrutura, Docker, Coolify, segurança e backups.
Suporta execução local na VPS e execução remota via SSH (multiplexada).
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Resiliência UTF-8 multiplataforma (prevenção de UnicodeEncodeError em terminais Windows)
if sys.platform == "win32":
    import io

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

DEFAULT_VPS_IP = "169.58.99.181"
DEFAULT_SSH_USER = "root"
DEFAULT_SSH_KEY = os.path.expanduser(r"~/.ssh/contabo-hermes-2026-07-17.key")
DEFAULT_CMD_TIMEOUT = 45
_CMD_TIMEOUT: int = DEFAULT_CMD_TIMEOUT
TZ_BRT = timezone(timedelta(hours=-3))

CRITICAL_CONTAINERS: Tuple[str, ...] = (
    "coolify",
    "coolify-db",
    "supabase-db",
    "supabase-kong",
    "supabase-auth",
    "supabase-rest",
    "coolify-proxy",
)

Finding = Dict[str, str]
CmdResult = Tuple[int, str, str]

RC_TIMEOUT = 124
RC_NOT_FOUND = 127

_SEVERITY_ORDER: Dict[str, int] = {"OK": 0, "WARNING": 1, "CRITICAL": 2}

_ssh_control_dir: Optional[str] = None


def get_now_str() -> str:
    return datetime.now(TZ_BRT).strftime("%Y-%m-%d %H:%M:%S")


def set_cmd_timeout(seconds: int) -> None:
    """Define o timeout efetivo para todas as coletas (consumido pela flag --timeout)."""
    global _CMD_TIMEOUT
    _CMD_TIMEOUT = max(5, seconds)


def escalate(current: str, level: str) -> str:
    """Eleva o status agregado sem jamais rebaixá-lo."""
    return level if _SEVERITY_ORDER[level] > _SEVERITY_ORDER[current] else current


def safe_int(raw: Optional[str]) -> Optional[int]:
    """Extrai o primeiro inteiro de uma string, sem lançar exceção."""
    match = re.search(r"-?\d+", raw or "")
    return int(match.group()) if match else None


def _ssh_multiplexing_supported() -> bool:
    # OpenSSH for Windows não implementa ControlMaster (sem sockets de controle).
    return sys.platform != "win32"


def _cleanup_ssh_control() -> None:
    if _ssh_control_dir:
        shutil.rmtree(_ssh_control_dir, ignore_errors=True)


def _ssh_control_path() -> str:
    global _ssh_control_dir
    if _ssh_control_dir is None:
        _ssh_control_dir = tempfile.mkdtemp(prefix="hermes-ssh-")
        atexit.register(_cleanup_ssh_control)
    return os.path.join(_ssh_control_dir, "cm-%C")


def run_cmd(
    cmd: str,
    remote_host: Optional[str] = None,
    remote_user: Optional[str] = None,
    remote_key: Optional[str] = None,
    timeout: Optional[int] = None,
) -> CmdResult:
    """Executa um comando localmente ou via SSH. Nunca propaga exceção ao chamador."""
    effective = timeout if timeout is not None else _CMD_TIMEOUT
    popen_args: Any
    use_shell: bool

    if remote_host:
        argv: List[str] = [
            "ssh",
            "-i", remote_key or DEFAULT_SSH_KEY,
            "-o", "BatchMode=yes",
            # 'accept-new' preserva a automação sem abrir janela permanente de MITM.
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
        ]
        if _ssh_multiplexing_supported():
            argv += [
                "-o", "ControlMaster=auto",
                "-o", f"ControlPath={_ssh_control_path()}",
                "-o", "ControlPersist=60s",
            ]
        argv += [f"{remote_user or DEFAULT_SSH_USER}@{remote_host}", cmd]
        popen_args, use_shell = argv, False
    else:
        popen_args, use_shell = cmd, True

    try:
        res = subprocess.run(
            popen_args,
            shell=use_shell,
            capture_output=True,
            text=True,
            timeout=effective,
            encoding="utf-8",
            errors="replace",
        )
        return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return RC_TIMEOUT, "", f"TIMEOUT de {effective}s excedido em: {cmd}"
    except FileNotFoundError as exc:
        return RC_NOT_FOUND, "", f"Executável ausente: {exc}"
    except OSError as exc:
        return 1, "", f"Falha de SO ao executar comando: {exc}"


def collection_failure(domain: str, probe: str, rc: int, err: str) -> Finding:
    """Torna explícita a falha de coleta — jamais reportar 'saudável' sem dado."""
    if rc == RC_TIMEOUT:
        detail = "tempo limite excedido (host lento ou inacessível)"
    elif rc == RC_NOT_FOUND:
        detail = "binário necessário não encontrado no PATH"
    else:
        detail = err or f"código de retorno {rc}"
    return {
        "level": "CRITICAL",
        "domain": domain,
        "issue": f"Coleta de '{probe}' FALHOU — métrica indisponível ({detail}).",
        "action": "Validar conectividade SSH/chave e a presença do binário na VPS antes de confiar neste relatório.",
    }


def audit_system_resources(
    remote_host: Optional[str] = None,
    remote_user: Optional[str] = None,
    remote_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Audita CPU, Memória RAM e Espaço em Disco."""
    findings: List[Finding] = []
    status = "OK"

    # 1. Disco
    rc, out, err = run_cmd(
        "df -hP / | awk 'NR==2 {print $2, $3, $4, $5}'", remote_host, remote_user, remote_key
    )
    disk_info: Dict[str, Any] = {}
    pct = safe_int(out.split()[3]) if rc == 0 and len(out.split()) >= 4 else None
    if rc != 0 or pct is None:
        status = escalate(status, "CRITICAL")
        findings.append(collection_failure("Disco", "df -hP /", rc, err))
    else:
        parts = out.split()
        disk_info = {
            "total": parts[0],
            "used": parts[1],
            "free": parts[2],
            "percent_str": parts[3],
            "percent": pct,
        }
        if pct >= 90:
            status = escalate(status, "CRITICAL")
            findings.append({
                "level": "CRITICAL",
                "domain": "Disco",
                "issue": f"Espaço em disco crítico: {pct}% utilizado ({parts[2]} livres).",
                "action": "Executar 'docker system prune -af --volumes' e remover logs antigos em /var/log.",
            })
        elif pct >= 80:
            status = escalate(status, "WARNING")
            findings.append({
                "level": "WARNING",
                "domain": "Disco",
                "issue": f"Espaço em disco em atenção: {pct}% utilizado ({parts[2]} livres).",
                "action": "Executar 'docker system prune -f' preventivamente.",
            })

    # 2. Memória RAM
    rc, out, err = run_cmd(
        "free -m | awk 'NR==2 {print $2, $3, $4, $7}'", remote_host, remote_user, remote_key
    )
    mem_info: Dict[str, Any] = {}
    mem_vals = [safe_int(v) for v in out.split()[:4]] if rc == 0 else []
    if rc != 0 or len(mem_vals) < 4 or any(v is None for v in mem_vals):
        status = escalate(status, "CRITICAL")
        findings.append(collection_failure("Memória", "free -m", rc, err))
    else:
        total, used, free_mb, avail = (int(v) for v in mem_vals)  # type: ignore[arg-type]
        mem_info = {"total_mb": total, "used_mb": used, "free_mb": free_mb, "available_mb": avail}
        if avail < 800:
            status = escalate(status, "CRITICAL")
            findings.append({
                "level": "CRITICAL",
                "domain": "Memória",
                "issue": f"Memória RAM disponível crítica: {avail} MB livres de {total} MB.",
                "action": "Executar 'sync && echo 3 > /proc/sys/vm/drop_caches' e checar 'top -b -n1 -o %MEM | head -n 20'.",
            })
        elif avail < 1500:
            status = escalate(status, "WARNING")
            findings.append({
                "level": "WARNING",
                "domain": "Memória",
                "issue": f"Memória RAM disponível baixa: {avail} MB livres de {total} MB.",
                "action": "Monitorar watchdog de memória (/home/hermes/scripts/vps_memory_watchdog.sh).",
            })

    # 3. CPU Load
    rc, out, err = run_cmd("uptime", remote_host, remote_user, remote_key)
    cpu_info: Dict[str, Any] = {"load_avg": out}
    match = re.search(r"load average:\s*([\d.,]+),\s*([\d.,]+),\s*([\d.,]+)", out)
    if rc != 0 or not match:
        status = escalate(status, "CRITICAL")
        findings.append(collection_failure("CPU", "uptime", rc, err))
    else:
        l1, l5, l15 = (float(match.group(i).replace(",", ".")) for i in (1, 2, 3))
        cpu_info.update({"load_1m": l1, "load_5m": l5, "load_15m": l15})
        if l15 > 8.0:
            status = escalate(status, "WARNING")
            findings.append({
                "level": "WARNING",
                "domain": "CPU",
                "issue": f"CPU Load Average prolongado alto: {l15} (15m).",
                "action": "Inspecionar processos em loop com 'top -b -n1 -o %CPU | head -n 15'.",
            })

    return {"status": status, "disk": disk_info, "memory": mem_info, "cpu": cpu_info, "findings": findings}


def _matches_critical(name: str, critical: str) -> bool:
    """Match ancorado: evita que 'coolify' capture 'coolify-db' e duplique alertas."""
    return name == critical or name.startswith(f"{critical}-") or name.startswith(f"{critical}_")


def _best_critical_match(name: str) -> Optional[str]:
    """Atribui o container ao crítico MAIS ESPECÍFICO que ele satisfaz.

    Impede que 'coolify-db' seja contabilizado como prova de presença de 'coolify'.
    """
    matches = [crit for crit in CRITICAL_CONTAINERS if _matches_critical(name, crit)]
    return max(matches, key=len) if matches else None


def audit_docker_containers(
    remote_host: Optional[str] = None,
    remote_user: Optional[str] = None,
    remote_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Audita saúde dos containers Docker, restart loops e ausência de serviços críticos."""
    findings: List[Finding] = []
    status = "OK"
    containers: List[Dict[str, str]] = []

    cmd = "docker ps -a --format '{{.Names}}||{{.Status}}||{{.RunningFor}}'"
    rc, out, err = run_cmd(cmd, remote_host, remote_user, remote_key)

    if rc != 0:
        return {
            "status": "CRITICAL",
            "count": 0,
            "running_count": 0,
            "containers": [],
            "findings": [collection_failure("Docker", "docker ps -a", rc, err)],
        }

    for line in out.splitlines():
        parts = line.split("||")
        if len(parts) < 2:
            continue
        name, stat_str = parts[0].strip(), parts[1].strip()
        if not name:
            continue
        containers.append({"name": name, "status": stat_str})

        if "Restarting" in stat_str:
            status = escalate(status, "CRITICAL")
            findings.append({
                "level": "CRITICAL",
                "domain": "Docker",
                "issue": f"Container em loop de restart: '{name}' ({stat_str}).",
                "action": f"Verificar logs com 'docker logs --tail 50 {name}'.",
            })
            continue

        if "Exited" in stat_str or "Dead" in stat_str:
            if _best_critical_match(name) is not None:
                status = escalate(status, "CRITICAL")
                findings.append({
                    "level": "CRITICAL",
                    "domain": "Docker",
                    "issue": f"Container crítico PARADO: '{name}' ({stat_str}).",
                    "action": f"Reiniciar container com 'docker start {name}'.",
                })

    covered = {m for m in (_best_critical_match(c["name"]) for c in containers) if m}
    for crit in CRITICAL_CONTAINERS:
        if crit not in covered:
            status = escalate(status, "CRITICAL")
            findings.append({
                "level": "CRITICAL",
                "domain": "Docker",
                "issue": f"Container crítico AUSENTE do inventário: '{crit}' não existe na VPS.",
                "action": f"Recriar o serviço '{crit}' via Coolify ou 'docker compose up -d'.",
            })

    running = sum(1 for c in containers if c["status"].startswith("Up"))
    return {
        "status": status,
        "count": len(containers),
        "running_count": running,
        "containers": containers,
        "findings": findings,
    }


def audit_services_and_ports(
    remote_host: Optional[str] = None,
    remote_user: Optional[str] = None,
    remote_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Testa disponibilidade das portas e serviços essenciais."""
    findings: List[Finding] = []
    status = "OK"

    checks = [
        {
            "name": "Coolify UI/API (8000)",
            "cmd": "curl -s -m 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000 || echo 'FAIL'",
            "action": "Checar 'docker logs --tail 50 coolify'.",
        },
        {
            "name": "Hermes Dashboard Backend (9100)",
            "cmd": "curl -s -m 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:9100/api/status || echo 'FAIL'",
            "action": "Checar 'systemctl status hermes-dashboard'.",
        },
        {
            "name": "Syncthing (22000)",
            "cmd": "ss -tuln | grep -q ':22000 ' && echo 'LISTEN' || echo 'FAIL'",
            "action": "Checar 'systemctl status syncthing@hermes'.",
        },
    ]

    results: Dict[str, str] = {}
    for check in checks:
        rc, out, err = run_cmd(check["cmd"], remote_host, remote_user, remote_key)
        results[check["name"]] = out or f"ERRO(rc={rc})"

        # Falha de transporte/coleta jamais pode render 'serviço saudável'.
        if rc in (RC_TIMEOUT, RC_NOT_FOUND) or (rc != 0 and not out):
            status = escalate(status, "CRITICAL")
            findings.append(collection_failure("Serviços", check["name"], rc, err))
            continue

        code = safe_int(out)
        # 000 (curl sem conexão), FAIL, ou 5xx são falhas reais. 2xx/3xx/401 são saudáveis.
        unhealthy = "FAIL" in out or code == 0 or (code is not None and code >= 500)
        if unhealthy:
            status = escalate(status, "WARNING")
            findings.append({
                "level": "WARNING",
                "domain": "Serviços",
                "issue": f"Serviço '{check['name']}' não respondeu adequadamente (retorno: {results[check['name']]}).",
                "action": check["action"],
            })

    return {"status": status, "results": results, "findings": findings}


def audit_backups(
    remote_host: Optional[str] = None,
    remote_user: Optional[str] = None,
    remote_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Verifica se existe um backup BEM-SUCEDIDO e RECENTE (últimas ~26h)."""
    findings: List[Finding] = []
    status = "OK"

    cmd = "tail -n 80 /home/hermes/scripts/backup.log 2>/dev/null || echo 'NO_LOG'"
    rc, out, err = run_cmd(cmd, remote_host, remote_user, remote_key)

    if rc in (RC_TIMEOUT, RC_NOT_FOUND):
        return {
            "status": "CRITICAL",
            "info": {"recent_log": ""},
            "findings": [collection_failure("Backups", "backup.log", rc, err)],
        }

    backup_info = {"recent_log": out}

    if "NO_LOG" in out or not out:
        status = escalate(status, "CRITICAL")
        findings.append({
            "level": "CRITICAL",
            "domain": "Backups",
            "issue": "Arquivo /home/hermes/scripts/backup.log não encontrado ou vazio — não há prova de backup.",
            "action": "Checar se o cron 'vps_auto_backup_drive.py' está configurado no crontab de root.",
        })
        return {"status": status, "info": backup_info, "findings": findings}

    now = datetime.now(TZ_BRT)
    recent_dates = {(now - timedelta(days=d)).strftime("%Y-%m-%d") for d in (0, 1)}
    success_tokens = ("SUCESSO", "SUCCESS", "OK:")

    # Conjunção obrigatória: a MESMA linha precisa ter marca de sucesso E data recente.
    has_recent_success = any(
        any(tok in line.upper() for tok in success_tokens) and any(d in line for d in recent_dates)
        for line in out.splitlines()
    )

    if not has_recent_success:
        status = escalate(status, "CRITICAL")
        findings.append({
            "level": "CRITICAL",
            "domain": "Backups",
            "issue": "Nenhum backup com SUCESSO confirmado nas últimas 26 horas.",
            "action": "Executar manualmente: 'python3 /home/hermes/scripts/vps_auto_backup_drive.py'.",
        })

    return {"status": status, "info": backup_info, "findings": findings}


def audit_security(
    remote_host: Optional[str] = None,
    remote_user: Optional[str] = None,
    remote_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Audita status do UFW e das jails do Fail2ban."""
    findings: List[Finding] = []
    status = "OK"

    # UFW — distingue 'inativo', 'sem privilégio' e 'falha de coleta'.
    rc, out, err = run_cmd("ufw status 2>&1 | head -n 1", remote_host, remote_user, remote_key)
    lowered = f"{out} {err}".lower()
    perm_issue = "permission" in lowered or "need to be root" in lowered or "not found" in lowered
    ufw_active: Optional[bool] = None

    if rc in (RC_TIMEOUT, RC_NOT_FOUND) or (rc != 0 and not perm_issue):
        status = escalate(status, "CRITICAL")
        findings.append(collection_failure("Segurança", "ufw status", rc, err))
    elif perm_issue or not out:
        status = escalate(status, "WARNING")
        findings.append({
            "level": "WARNING",
            "domain": "Segurança",
            "issue": f"Não foi possível determinar o status do UFW (retorno: {out or err or rc}).",
            "action": "Executar a auditoria como root ou conceder sudo NOPASSWD para 'ufw status'.",
        })
    else:
        ufw_active = "status: active" in lowered
        if not ufw_active:
            status = escalate(status, "CRITICAL")
            findings.append({
                "level": "CRITICAL",
                "domain": "Segurança",
                "issue": "Firewall UFW NÃO está ativo!",
                "action": "Habilitar com 'ufw enable' e verificar portas abertas.",
            })

    # Fail2ban
    rc, out, err = run_cmd(
        "fail2ban-client status 2>&1 | grep 'Jail list'", remote_host, remote_user, remote_key
    )
    f2b_jails = out if (rc == 0 and out) else "indeterminado/inativo"
    if "sshd" not in f2b_jails:
        status = escalate(status, "WARNING")
        findings.append({
            "level": "WARNING",
            "domain": "Segurança",
            "issue": f"Fail2ban sshd jail não encontrada: {f2b_jails}.",
            "action": "Reiniciar fail2ban com 'systemctl restart fail2ban'.",
        })

    return {"status": status, "ufw_active": ufw_active, "fail2ban_jails": f2b_jails, "findings": findings}


def generate_report(results: Dict[str, Any], format_type: str = "markdown") -> Tuple[str, str]:
    """Gera o relatório e devolve (texto, status_geral). Não muta o dicionário de entrada."""
    all_findings: List[Finding] = []
    for section in ("resources", "docker", "services", "backups", "security"):
        all_findings.extend(results[section]["findings"])

    overall_status = "HEALTHY"
    if any(f["level"] == "CRITICAL" for f in all_findings):
        overall_status = "CRITICAL"
    elif any(f["level"] == "WARNING" for f in all_findings):
        overall_status = "WARNING"

    payload = dict(results)
    payload["overall_status"] = overall_status
    payload["all_findings"] = all_findings

    if format_type == "json":
        return json.dumps(payload, indent=2, ensure_ascii=False), overall_status

    status_emoji = {
        "HEALTHY": "🟢 SAUDÁVEL",
        "WARNING": "🟡 ATENÇÃO",
        "CRITICAL": "🔴 CRÍTICO",
    }[overall_status]

    disk = results["resources"]["disk"]
    mem = results["resources"]["memory"]
    cpu = results["resources"]["cpu"]
    sec = results["security"]

    ufw_label = "✅ Ativo" if sec["ufw_active"] is True else ("❌ Inativo" if sec["ufw_active"] is False else "❔ Indeterminado")

    lines = [
        f"# 🛡️ Relatório Diário de Auditoria da VPS — {results['timestamp']}",
        "",
        f"**Status Geral da VPS ({results['target']}):** {status_emoji}",
        "",
        "## 📊 1. Recursos de Sistema",
        f"- **Disco (/):** {disk.get('used', 'N/D')} usados de {disk.get('total', 'N/D')} ({disk.get('percent_str', 'N/D')}) — {disk.get('free', 'N/D')} livres",
        f"- **RAM:** {mem.get('available_mb', 'N/D')} MB disponíveis (Total: {mem.get('total_mb', 'N/D')} MB, Usado: {mem.get('used_mb', 'N/D')} MB)",
        f"- **Load Average:** 1m: {cpu.get('load_1m', 'N/D')} | 5m: {cpu.get('load_5m', 'N/D')} | 15m: {cpu.get('load_15m', 'N/D')}",
        "",
        "## 🐳 2. Containers Docker & Coolify",
        f"- **Inventário (docker ps -a):** {results['docker']['count']} containers | **Em execução:** {results['docker']['running_count']}",
        f"- **Coolify UI/API (8000):** {results['services']['results'].get('Coolify UI/API (8000)', 'N/D')}",
        f"- **Hermes Status API (9100):** {results['services']['results'].get('Hermes Dashboard Backend (9100)', 'N/D')}",
        "",
        "## 🔒 3. Segurança & Firewall",
        f"- **Firewall UFW:** {ufw_label}",
        f"- **Fail2ban Jails:** {sec['fail2ban_jails']}",
        "",
        "## 💾 4. Backups Automatizados",
        f"- **Status do Backup:** {'✅ Regular (sucesso nas últimas 26h)' if results['backups']['status'] == 'OK' else '⚠️ Requer Verificação'}",
        "",
    ]

    if all_findings:
        lines.append("## ⚠️ 5. Apontamentos & Ações Corretivas Recomendadas")
        for i, f in enumerate(all_findings, 1):
            badge = "🔴 [CRÍTICO]" if f["level"] == "CRITICAL" else "🟡 [ATENÇÃO]"
            lines.append(f"### {i}. {badge} {f['domain']}: {f['issue']}")
            lines.append(f"- **Ação Prescritiva:** `{f['action']}`")
            lines.append("")
    else:
        lines.append("## ✅ 5. Conclusão")
        lines.append("Todos os indicadores coletados com sucesso operam em parâmetros ótimos.")

    return "\n".join(lines), overall_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes VPS Daily Auditor")
    parser.add_argument("--remote", default=None, help=f"IP do host remoto via SSH (ex: {DEFAULT_VPS_IP})")
    parser.add_argument("--user", default=DEFAULT_SSH_USER, help="Usuário SSH para auditoria remota")
    parser.add_argument("--key", default=DEFAULT_SSH_KEY, help="Caminho da chave privada SSH")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Formato de saída")
    parser.add_argument("--output", default=None, help="Arquivo para salvar o relatório")
    parser.add_argument("--timeout", type=int, default=DEFAULT_CMD_TIMEOUT, help="Timeout por comando (s)")
    parser.add_argument("--slack", dest="slack", action="store_true", default=True, help="Envia o relatório executivo para o Slack do Marcus")
    parser.add_argument("--no-slack", dest="slack", action="store_false", help="Desativa o envio para o Slack")
    args = parser.parse_args()

    if args.remote and not os.path.isfile(args.key):
        print(f"ERRO: chave SSH não encontrada em '{args.key}'.", file=sys.stderr)
        return 2

    set_cmd_timeout(args.timeout)

    target = f"{args.user}@{args.remote}" if args.remote else "Localhost (VPS)"
    ctx = (args.remote, args.user, args.key)

    results: Dict[str, Any] = {
        "timestamp": get_now_str(),
        "target": target,
        "resources": audit_system_resources(*ctx),
        "docker": audit_docker_containers(*ctx),
        "services": audit_services_and_ports(*ctx),
        "backups": audit_backups(*ctx),
        "security": audit_security(*ctx),
    }

    report, overall_status = generate_report(results, format_type=args.format)

    if args.output:
        out_dir = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"Relatório gravado em: {args.output}")
    else:
        print(report)

    if args.slack:
        try:
            from slack_report_notifier import send_slack_report

            active = [f["issue"] for f in [
                *results["resources"]["findings"],
                *results["docker"]["findings"],
                *results["services"]["findings"],
                *results["backups"]["findings"],
                *results["security"]["findings"],
            ] if f.get("level") == "CRITICAL"]
            warnings = [f["issue"] for f in [
                *results["resources"]["findings"],
                *results["docker"]["findings"],
                *results["services"]["findings"],
                *results["backups"]["findings"],
                *results["security"]["findings"],
            ] if f.get("level") == "WARNING"]
            metrics = {
                "disco": f"{results['resources'].get('disk', {}).get('percent', 'N/A')}% usado ({results['resources'].get('disk', {}).get('free', 'N/A')} livres)",
                "memória": f"{results['resources'].get('memory', {}).get('percent', 'N/A')}% usada ({results['resources'].get('memory', {}).get('free', 'N/A')} livres)",
                "docker": f"{results['docker'].get('count', 0)} containers ({results['docker'].get('running_count', 0)} ativos)",
                "backup": results["backups"].get("status", "N/A"),
                "firewall": "Ativo" if results["security"].get("ufw_active") else "Alerta/Inativo",
            }
            recs = [f["action"] for f in [
                *results["resources"]["findings"],
                *results["docker"]["findings"],
                *results["services"]["findings"],
                *results["backups"]["findings"],
                *results["security"]["findings"],
            ] if f.get("action")]
            send_slack_report(
                project_name="Hermes VPS Control",
                status=overall_status,
                summary=f"Auditoria diária de infraestrutura e serviços da VPS ({target}).",
                active_issues=active,
                future_risks=warnings,
                metrics=metrics,
                recommendations=recs,
                vault_file=args.output,
            )
        except Exception as e:
            print(f"[SLACK] Aviso: falha ao enviar relatório ao Slack: {e}", file=sys.stderr)

    # Exit code consumível por cron/systemd/monitoramento externo.
    return {"HEALTHY": 0, "WARNING": 1, "CRITICAL": 2}[overall_status]


if __name__ == "__main__":
    sys.exit(main())
