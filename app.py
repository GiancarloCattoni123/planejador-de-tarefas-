"""
╔══════════════════════════════════════════════════════════════╗
║  app.py — Servidor Flask com autenticação                    ║
║                                                              ║
║  Cada usuário tem seu próprio histórico e tarefas.           ║
║  Login por usuário/senha com sessão Flask.                   ║
║  Usuários salvos em users.json (criado automaticamente).     ║
╚══════════════════════════════════════════════════════════════╝
"""

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from planejador import call_api, parse_response, update_state, build_payload, Task
import anthropic
import os, json, hashlib

# ──────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ──────────────────────────────────────────────────────────────
app            = Flask(__name__)
app.secret_key = "planejador-secret-2025-reset"
WINDOW_SIZE    = 5
USERS_FILE     = os.path.join(os.path.dirname(__file__), "users.json")
DATA_DIR       = os.path.join(os.path.dirname(__file__), "userdata")


os.makedirs(DATA_DIR, exist_ok=True)


# ──────────────────────────────────────────────────────────────
# GERENCIAMENTO DE USUÁRIOS
# ──────────────────────────────────────────────────────────────
def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()

def carregar_usuarios() -> dict:
    if not os.path.exists(USERS_FILE):
        usuarios = {
            "admin": hash_senha("admin"),
            "joao":  hash_senha("5678"),
            "ana":   hash_senha("1234"),
        }
        salvar_usuarios(usuarios)
        return usuarios
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def salvar_usuarios(usuarios: dict):
    with open(USERS_FILE, "w") as f:
        json.dump(usuarios, f, indent=2)

def usuario_existe(username: str) -> bool:
    return username.lower() in carregar_usuarios()

def senha_correta(username: str, senha: str) -> bool:
    return carregar_usuarios().get(username.lower()) == hash_senha(senha)

def cadastrar_usuario(username: str, senha: str):
    usuarios = carregar_usuarios()
    usuarios[username.lower()] = hash_senha(senha)
    salvar_usuarios(usuarios)


# ──────────────────────────────────────────────────────────────
# DADOS POR USUÁRIO
# ──────────────────────────────────────────────────────────────
def get_user_file(username: str) -> str:
    return os.path.join(DATA_DIR, f"{username.lower()}.json")

def carregar_dados_usuario(username: str) -> dict:
    path = get_user_file(username)
    if not os.path.exists(path):
        return {"history": [], "tasks": [], "total_tokens": 0}
    with open(path, "r") as f:
        return json.load(f)

def salvar_dados_usuario(username: str, dados: dict):
    with open(get_user_file(username), "w") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)

def get_tasks_obj(dados: dict) -> list:
    tasks = []
    for t in dados.get("tasks", []):
        task         = Task(t["name"], t.get("time"))
        task.done    = t.get("done", False)
        task.created = t.get("created", "")
        tasks.append(task)
    return tasks

def tasks_to_dict(tasks: list) -> list:
    return [{"name": t.name, "time": t.time, "done": t.done, "created": t.created} for t in tasks]


# ──────────────────────────────────────────────────────────────
# PROTEÇÃO DE ROTAS
# ──────────────────────────────────────────────────────────────
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


# ──────────────────────────────────────────────────────────────
# ROTAS DE AUTENTICAÇÃO
# ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    print(f"SESSION: {dict(session)}")
    if "username" not in session:
        print("REDIRECIONANDO PARA LOGIN")
        return redirect(url_for("login_page"))
    print(f"USUARIO LOGADO: {session['username']}")
    return render_template("index.html", username=session["username"])

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/api/login", methods=["POST"])
def login():
    data     = request.get_json()
    username = data.get("username", "").strip().lower()
    senha    = data.get("password", "").strip()

    if not username or not senha:
        return jsonify({"ok": False, "error": "Preencha todos os campos."})
    if not usuario_existe(username):
        return jsonify({"ok": False, "error": "Usuário não encontrado."})
    if not senha_correta(username, senha):
        return jsonify({"ok": False, "error": "Senha incorreta."})

    session["username"] = username
    return jsonify({"ok": True})

@app.route("/api/cadastro", methods=["POST"])
def cadastro():
    data     = request.get_json()
    username = data.get("username", "").strip().lower()
    senha    = data.get("password", "").strip()
    confirma = data.get("confirm", "").strip()

    if not username or not senha:
        return jsonify({"ok": False, "error": "Preencha todos os campos."})
    if len(username) < 3:
        return jsonify({"ok": False, "error": "Nome deve ter pelo menos 3 caracteres."})
    if len(senha) < 4:
        return jsonify({"ok": False, "error": "Senha deve ter pelo menos 4 caracteres."})
    if senha != confirma:
        return jsonify({"ok": False, "error": "As senhas não coincidem."})
    if usuario_existe(username):
        return jsonify({"ok": False, "error": "Este nome já está em uso."})

    cadastrar_usuario(username, senha)
    session["username"] = username
    return jsonify({"ok": True})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


# ──────────────────────────────────────────────────────────────
# ROTAS PRINCIPAIS
# ──────────────────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    username   = session["username"]
    user_input = request.get_json().get("message", "").strip()
    if not user_input:
        return jsonify({"error": "Mensagem vazia"}), 400

    dados    = carregar_dados_usuario(username)
    history  = dados["history"]
    tasks    = get_tasks_obj(dados)
    messages = build_payload(history, user_input, WINDOW_SIZE)

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        raw_text, in_tok, out_tok = call_api(client, messages)
    except anthropic.APIConnectionError:
        return jsonify({"error": "Erro de conexão com a API."}), 503
    except anthropic.AuthenticationError:
        return jsonify({"error": "Chave de API inválida."}), 401
    except anthropic.RateLimitError:
        return jsonify({"error": "Rate limit atingido. Tente em instantes."}), 429
    except anthropic.APIStatusError as e:
        return jsonify({"error": f"Erro da API: {e.message}"}), 500

    reply, action = parse_response(raw_text)

    history.append({"role": "user",      "content": user_input})
    history.append({"role": "assistant", "content": raw_text})

    update_state(action, tasks)

    total = dados.get("total_tokens", 0) + in_tok + out_tok
    salvar_dados_usuario(username, {
        "history":      history,
        "tasks":        tasks_to_dict(tasks),
        "total_tokens": total,
    })

    return jsonify({
        "reply":  reply,
        "tasks":  tasks_to_dict(tasks),
        "tokens": {"input": in_tok, "output": out_tok, "total": total}
    })


@app.route("/api/estado")
@login_required
def estado():
    username = session["username"]
    dados    = carregar_dados_usuario(username)
    msgs     = [{"role": m["role"], "content": m["content"]} for m in dados.get("history", [])]
    return jsonify({
        "username": username,
        "messages": msgs,
        "tasks":    dados.get("tasks", []),
        "tokens":   {"total": dados.get("total_tokens", 0)}
    })


@app.route("/api/clear", methods=["POST"])
@login_required
def clear():
    salvar_dados_usuario(session["username"], {"history": [], "tasks": [], "total_tokens": 0})
    return jsonify({"ok": True})


# ──────────────────────────────────────────────────────────────
# INICIALIZAÇÃO
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)