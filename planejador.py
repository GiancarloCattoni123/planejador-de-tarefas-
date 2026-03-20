"""
╔══════════════════════════════════════════════════════════════╗
║  planejador.py — Lógica central (sem loop de terminal)       ║
║  Agora é importado pelo app.py em vez de rodar sozinho.      ║
║  Todas as funções permanecem iguais — só o main() foi        ║
║  removido, pois o loop agora vive no servidor Flask.         ║
╚══════════════════════════════════════════════════════════════╝
"""

import json
import re
import anthropic
from datetime import datetime

MODEL      = "claude-sonnet-4-6"
MAX_TOKENS = 1024

SYSTEM_PROMPT = """Você é um assistente de planejamento de tarefas diárias, simpático e objetivo.

Seu papel é ajudar o usuário a:
- Adicionar novas tarefas (com hora opcional)
- Marcar tarefas como concluídas
- Remover tarefas
- Listar tarefas pendentes e concluídas
- Sugerir prioridades e organizar o dia

SEMPRE responda em JSON válido com exatamente esta estrutura:
{
  "reply": "<sua resposta natural em português>",
  "action": {
    "type": "<add | complete | remove | list | none>",
    "task": "<nome da tarefa, se aplicável>",
    "time": "<horário no formato HH:MM, se mencionado, senão null>"
  }
}

Regras de ação:
- "add"      → usuário quer adicionar uma tarefa
- "complete" → usuário quer marcar uma tarefa como concluída
- "remove"   → usuário quer remover/deletar uma tarefa
- "list"     → usuário quer ver as tarefas
- "none"     → conversa geral, sem ação sobre tarefas

Se não houver ação: "action": {"type": "none", "task": null, "time": null}
Nunca invente tarefas. Só adicione o que o usuário pediu explicitamente."""


class Task:
    def __init__(self, name: str, time: str | None = None):
        self.name    = name
        self.time    = time
        self.done    = False
        self.created = datetime.now().strftime("%H:%M")


def build_payload(history: list[dict], user_message: str, window_size: int = 5) -> list[dict]:
    """Aplica sliding window e adiciona a mensagem atual."""
    windowed = history[-(window_size * 2):]
    return windowed + [{"role": "user", "content": user_message}]


def call_api(client: anthropic.Anthropic, messages: list[dict]) -> tuple[str, int, int]:
    """Chama a API e retorna (texto, tokens_entrada, tokens_saída)."""
    response = client.messages.create(
        model      = MODEL,
        max_tokens = MAX_TOKENS,
        system     = SYSTEM_PROMPT,
        messages   = messages,
    )
    return (
        response.content[0].text,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )


def parse_response(text: str) -> tuple[str, dict]:
    """Extrai reply e action do JSON. Fallback por keywords se falhar."""
    try:
        clean  = re.sub(r"```json|```", "", text).strip()
        data   = json.loads(clean)
        reply  = data.get("reply", text)
        action = data.get("action", {"type": "none", "task": None, "time": None})
        return reply, action
    except (json.JSONDecodeError, KeyError):
        lower  = text.lower()
        action = {"type": "none", "task": None, "time": None}
        if any(w in lower for w in ["adicionei", "adicionando", "tarefa criada"]):
            action["type"] = "add"
        elif any(w in lower for w in ["concluída", "concluído", "marcada como feita"]):
            action["type"] = "complete"
        return text, action


def update_state(action: dict, tasks: list) -> str | None:
    """Aplica a ação na lista de tarefas."""
    a_type = action.get("type", "none")
    a_task = action.get("task")
    a_time = action.get("time")

    if a_type == "add" and a_task:
        tasks.append(Task(a_task, a_time))

    elif a_type == "complete" and a_task:
        for t in tasks:
            if a_task.lower() in t.name.lower():
                t.done = True
                return None
        return f"Tarefa '{a_task}' não encontrada."

    elif a_type == "remove" and a_task:
        before   = len(tasks)
        tasks[:] = [t for t in tasks if a_task.lower() not in t.name.lower()]
        if len(tasks) == before:
            return f"Tarefa '{a_task}' não encontrada."

    return None