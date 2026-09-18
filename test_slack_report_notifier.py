from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any
import pytest
import slack_report_notifier as s


def test_get_status_emoji():
    assert s.get_status_emoji("OK") == "🟢"
    assert s.get_status_emoji("HEALTHY") == "🟢"
    assert s.get_status_emoji("SAUDÁVEL") == "🟢"
    assert s.get_status_emoji("WARNING") == "🟡"
    assert s.get_status_emoji("ATENÇÃO") == "🟡"
    assert s.get_status_emoji("CRITICAL") == "🔴"
    assert s.get_status_emoji("CRÍTICO") == "🔴"
    assert s.get_status_emoji("UNKNOWN_STATUS") == "⚪"


def test_build_block_kit_report_structure():
    fallback, blocks = s.build_block_kit_report(
        project_name="Hermes VPS Control",
        status="OK",
        summary="Varredura matinal sem falhas.",
        active_issues=[],
        future_risks=[],
        vault_file="Hermes-VPS-Control-2026-09-17.md",
    )
    assert "🟢 [OK] Auditoria 360° — Hermes VPS Control" in fallback
    assert blocks[0]["type"] == "header"
    assert "Hermes VPS Control" in blocks[0]["text"]["text"]
    assert any(b["type"] == "divider" for b in blocks)
    assert any(b.get("type") == "section" and "Zero falhas identificadas" in b.get("text", {}).get("text", "") for b in blocks)


def test_build_block_kit_with_issues_and_risks():
    issues = ["Porta 8000 inacessível", "Container parado"]
    risks = ["Disco acima de 85%", "Deploy pendente"]
    recs = ["Reiniciar container Coolify", "Limpar volumes docker"]

    fallback, blocks = s.build_block_kit_report(
        project_name="Araujo Make",
        status="CRÍTICO",
        summary="Problemas encontrados no backend.",
        active_issues=issues,
        future_risks=risks,
        recommendations=recs,
    )
    assert "🔴 [CRÍTICO]" in fallback
    issues_block = [b for b in blocks if "Falhas Ativas (2)" in b.get("text", {}).get("text", "")]
    assert len(issues_block) == 1
    assert "Porta 8000 inacessível" in issues_block[0]["text"]["text"]

    risks_block = [b for b in blocks if "Riscos Futuros & Alertas (2)" in b.get("text", {}).get("text", "")]
    assert len(risks_block) == 1
    assert "Disco acima de 85%" in risks_block[0]["text"]["text"]

    recs_block = [b for b in blocks if "Plano de Ação Sugerido" in b.get("text", {}).get("text", "")]
    assert len(recs_block) == 1
    assert "Reiniciar container Coolify" in recs_block[0]["text"]["text"]


def test_build_block_kit_with_metrics():
    metrics = {
        "disco": "37% usado (122G livres)",
        "ram": "53% livre (6GB)",
        "docker": "27 containers ativos",
    }
    fallback, blocks = s.build_block_kit_report(
        project_name="UTM.AI",
        status="OK",
        summary="Tudo em ordem.",
        metrics=metrics,
    )
    fields_block = [b for b in blocks if "fields" in b]
    assert len(fields_block) == 1
    fields = fields_block[0]["fields"]
    assert len(fields) == 3
    assert any("*Disco:*" in f["text"] for f in fields)


def test_parse_obsidian_markdown_clean(tmp_path: Path):
    md_file = tmp_path / "UTM-AI-2026-09-17.md"
    content = """# 🛡️ Relatório de Auditoria Diária Completa 360° — UTM.AI
> **Data**: 2026-09-17 às 09:00:00 BRT  
> **Status Geral**: 🟢 **OK**  

## 1. 🚨 Falhas Ativas Identificadas
- ✅ **Zero falhas ativas!** Código-fonte e banco 100% operacionais.

## 2. 🔮 Riscos Futuros & Alertas Preventivos
- ✅ **Zero riscos futuros iminentes.**

## 3. 📊 Diagnóstico dos Componentes
| Camada | Diagnóstico Técnico |
| :--- | :--- |
| `vitest` | ✅ 231/231 testes verdes |
| `docker` | ✅ Container healthy |

## 4. 🛠️ Plano de Ação & Sugestões de Correção
- 💡 Nenhuma intervenção necessária hoje.
---
"""
    md_file.write_text(content, encoding="utf-8")
    parsed = s.parse_obsidian_audit_markdown(str(md_file))

    assert parsed["project_name"] == "UTM.AI"
    assert parsed["status"] == "OK"
    assert len(parsed["active_issues"]) == 0
    assert len(parsed["future_risks"]) == 0
    assert parsed["metrics"]["vitest"] == "✅ 231/231 testes verdes"
    assert len(parsed["recommendations"]) == 0


def test_parse_obsidian_markdown_with_issues(tmp_path: Path):
    md_file = tmp_path / "Hermes-VPS-Control-2026-09-17.md"
    content = """# 🛡️ Relatório de Auditoria Diária Completa 360° — Hermes VPS Control
> **Data**: 2026-09-17 às 09:00:02 BRT  
> **Status Geral**: 🟡 **ATENÇÃO**  

## 1. 🚨 Falhas Ativas Identificadas
- ❌ **Serviço Syncthing parado.**

## 2. 🔮 Riscos Futuros & Alertas Preventivos
- ⚠️ **Existem deploys com status 'failed' na fila do Coolify.**

## 4. 🛠️ Plano de Ação & Sugestões de Correção
- 💡 Inspecionar logs do Coolify.
---
"""
    md_file.write_text(content, encoding="utf-8")
    parsed = s.parse_obsidian_audit_markdown(str(md_file))

    assert parsed["project_name"] == "Hermes VPS Control"
    assert parsed["status"] == "ATENÇÃO"
    assert len(parsed["active_issues"]) == 1
    assert "Serviço Syncthing parado" in parsed["active_issues"][0]
    assert len(parsed["future_risks"]) == 1
    assert "status 'failed'" in parsed["future_risks"][0]
    assert len(parsed["recommendations"]) == 1
    assert "Inspecionar logs" in parsed["recommendations"][0]


class FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_post_to_slack_success_mock(monkeypatch: pytest.MonkeyPatch):
    fake_payload = json.dumps({"ok": True, "ts": "1789691234.5678"}).encode("utf-8")
    monkeypatch.setattr(s.urllib.request, "urlopen", lambda req, timeout=15: FakeResponse(fake_payload))

    ok = s.post_to_slack("Teste mock", channel="D0BJ49PDPV4", token="xoxb-fake")
    assert ok is True


def test_post_to_slack_api_error_mock(monkeypatch: pytest.MonkeyPatch):
    fake_payload = json.dumps({"ok": False, "error": "channel_not_found"}).encode("utf-8")
    monkeypatch.setattr(s.urllib.request, "urlopen", lambda req, timeout=15: FakeResponse(fake_payload))

    ok = s.post_to_slack("Teste mock", channel="D0BJ49PDPV4", token="xoxb-fake")
    assert ok is False


def test_post_to_slack_network_exception(monkeypatch: pytest.MonkeyPatch):
    def fake_open(*_a, **_kw):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(s.urllib.request, "urlopen", fake_open)
    ok = s.post_to_slack("Teste mock", channel="D0BJ49PDPV4", token="xoxb-fake")
    assert ok is False


def test_send_from_obsidian_nonexistent_file():
    ok = s.send_from_obsidian_markdown("caminho_inexistente_12345.md")
    assert ok is False
