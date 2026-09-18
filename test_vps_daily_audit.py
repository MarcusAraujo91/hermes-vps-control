"""Testes unitários dos parsers puros do auditor de VPS (sem tocar em rede/SSH)."""

from datetime import datetime, timedelta

import vps_daily_audit as v


def _patch_run(monkeypatch, mapping):
    def fake_run(cmd, *_a, **_kw):
        for key, value in mapping.items():
            if key in cmd:
                return value
        return (1, "", "sem mock")
    monkeypatch.setattr(v, "run_cmd", fake_run)


def test_escalate_nunca_rebaixa():
    assert v.escalate("CRITICAL", "WARNING") == "CRITICAL"
    assert v.escalate("OK", "WARNING") == "WARNING"


def test_safe_int_tolera_lixo():
    assert v.safe_int("91%") == 91
    assert v.safe_int("-") is None
    assert v.safe_int(None) is None


def test_falha_de_coleta_vira_critico(monkeypatch):
    _patch_run(monkeypatch, {})
    res = v.audit_system_resources(remote_host="1.2.3.4")
    assert res["status"] == "CRITICAL"
    assert len(res["findings"]) == 3


def test_container_critico_parado_gera_um_unico_alerta(monkeypatch):
    linhas = "\n".join(
        f"{n}||Up 2 days||2 days" for n in v.CRITICAL_CONTAINERS if n != "coolify-db"
    ) + "\ncoolify-db||Exited (1) 5 minutes ago||5 minutes"
    _patch_run(monkeypatch, {"docker ps -a": (0, linhas, "")})
    res = v.audit_docker_containers()
    parados = [f for f in res["findings"] if "PARADO" in f["issue"]]
    assert len(parados) == 1
    assert res["status"] == "CRITICAL"


def test_container_critico_ausente_e_detectado(monkeypatch):
    linhas = "\n".join(f"{n}||Up 1 day||1 day" for n in v.CRITICAL_CONTAINERS if n != "supabase-db")
    _patch_run(monkeypatch, {"docker ps -a": (0, linhas, "")})
    res = v.audit_docker_containers()
    assert any("AUSENTE" in f["issue"] for f in res["findings"])


def test_backup_com_erro_na_data_de_hoje_nao_passa(monkeypatch):
    hoje = datetime.now(v.TZ_BRT).strftime("%Y-%m-%d")
    _patch_run(monkeypatch, {"backup.log": (0, f"{hoje} 03:00 ERRO: falha no upload", "")})
    res = v.audit_backups()
    assert res["status"] == "CRITICAL"


def test_backup_com_sucesso_recente_passa(monkeypatch):
    hoje = datetime.now(v.TZ_BRT).strftime("%Y-%m-%d")
    _patch_run(monkeypatch, {"backup.log": (0, f"{hoje} 03:00 SUCESSO: enviado ao Drive", "")})
    assert v.audit_backups()["status"] == "OK"


def test_ufw_sem_permissao_nao_vira_falso_critico(monkeypatch):
    _patch_run(monkeypatch, {"ufw status": (1, "", "ERROR: You need to be root to run this script")})
    res = v.audit_security()
    assert res["ufw_active"] is None
    assert not any(f["level"] == "CRITICAL" for f in res["findings"])


def test_ausencia_de_coolify_nao_e_mascarada_por_coolify_db(monkeypatch):
    linhas = "\n".join(f"{n}||Up 1 day||1 day" for n in v.CRITICAL_CONTAINERS if n != "coolify")
    _patch_run(monkeypatch, {"docker ps -a": (0, linhas, "")})
    res = v.audit_docker_containers()
    assert any("'coolify'" in f["issue"] and "AUSENTE" in f["issue"] for f in res["findings"])
    assert res["status"] == "CRITICAL"


def test_ssh_morto_nao_reporta_servicos_saudaveis(monkeypatch):
    monkeypatch.setattr(v, "run_cmd", lambda *_a, **_k: (255, "", "ssh: connect timed out"))
    res = v.audit_services_and_ports(remote_host="1.2.3.4")
    assert res["status"] == "CRITICAL"
    assert len(res["findings"]) == 3


def test_flag_timeout_e_efetivamente_propagada(monkeypatch):
    v.set_cmd_timeout(7)
    try:
        capturado = {}

        def fake(*_a, **kw):
            capturado["t"] = kw["timeout"]
            raise KeyboardInterrupt

        monkeypatch.setattr(v.subprocess, "run", fake)
        try:
            v.run_cmd("echo x")
        except KeyboardInterrupt:
            pass
        assert capturado["t"] == 7
    finally:
        v.set_cmd_timeout(v.DEFAULT_CMD_TIMEOUT)
