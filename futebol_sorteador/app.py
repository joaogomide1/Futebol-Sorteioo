from __future__ import annotations

import os
import random
import string
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Iterable

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///futebol.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "chave-local-de-desenvolvimento")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Mantém o cookie mais seguro quando o app estiver em produção com HTTPS.
if os.environ.get("FLASK_ENV") == "production":
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

db = SQLAlchemy(app)

MATCH_DURATION_SECONDS = 8 * 60
GOAL_LIMIT = 3
TEAMS = ("X", "Y")
ROOM_CODE_ALPHABET = string.ascii_uppercase + string.digits
STATUS_LABELS = {
    "scheduled": "Marcada",
    "in_progress": "Ao vivo",
    "finished": "Encerrada",
}


class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(12), nullable=True, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive(), nullable=False)

    players = db.relationship("Player", back_populates="room")
    matches = db.relationship("Match", back_populates="room", cascade="all, delete-orphan")


class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("room.id"), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    rating = db.Column(db.Float, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive(), nullable=False)

    room = db.relationship("Room", back_populates="players")


class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("room.id"), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default="scheduled", nullable=False)  # scheduled, in_progress, finished
    duration_seconds = db.Column(db.Integer, default=MATCH_DURATION_SECONDS, nullable=False)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    score_x = db.Column(db.Integer, default=0, nullable=False)
    score_y = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive(), nullable=False)

    room = db.relationship("Room", back_populates="matches")
    assignments = db.relationship("MatchAssignment", back_populates="match", cascade="all, delete-orphan")
    events = db.relationship("GoalEvent", back_populates="match", cascade="all, delete-orphan", order_by="GoalEvent.created_at")


class MatchAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("match.id"), nullable=False)
    player_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=False)
    team = db.Column(db.String(1), nullable=False)  # X ou Y
    rating_snapshot = db.Column(db.Float, nullable=False)

    match = db.relationship("Match", back_populates="assignments")
    player = db.relationship("Player")


class GoalEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("match.id"), nullable=False)
    team = db.Column(db.String(1), nullable=False)
    scorer_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=False)
    assist_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=True)
    minute = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive(), nullable=False)

    match = db.relationship("Match", back_populates="events")
    scorer = db.relationship("Player", foreign_keys=[scorer_id])
    assist = db.relationship("Player", foreign_keys=[assist_id])


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_room_code(length: int = 6) -> str:
    return "".join(random.choice(ROOM_CODE_ALPHABET) for _ in range(length))


def normalize_room_code(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value.upper().strip() if ch.isalnum())


def generate_unique_room_code() -> str:
    for _ in range(100):
        code = generate_room_code()
        if Room.query.filter(func.upper(Room.code) == code).first() is None:
            return code
    return generate_room_code(8)


def safe_next_url(value: str | None, fallback: str) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return fallback


def parse_rating(value: str) -> float:
    try:
        rating = float(value.replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("A nota precisa ser um número entre 0 e 10.")
    if not 0 <= rating <= 10:
        raise ValueError("A nota precisa estar entre 0 e 10.")
    return round(rating, 2)


def parse_datetime_local(value: str | None) -> datetime:
    if not value:
        return utcnow_naive()
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError("Data e horário inválidos.")


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%d/%m/%Y %H:%M")


@app.template_filter("dt")
def dt_filter(value: datetime | None) -> str:
    return format_datetime(value)


@app.template_filter("score")
def score_filter(value: float | None) -> str:
    if value is None:
        return "0.00"
    text_value = f"{value:.2f}"
    return text_value.rstrip("0").rstrip(".")


@app.template_filter("pct")
def pct_filter(value: float | None) -> str:
    if value is None:
        return "0%"
    return f"{value:.0f}%"


@app.template_filter("status_label")
def status_label_filter(value: str) -> str:
    return STATUS_LABELS.get(value, value)


def team_average(assignments: Iterable[MatchAssignment], team: str) -> float:
    ratings = [a.rating_snapshot for a in assignments if a.team == team]
    if not ratings:
        return 0.0
    return sum(ratings) / len(ratings)


def assignment_map(match: Match) -> dict[int, MatchAssignment]:
    return {assignment.player_id: assignment for assignment in match.assignments}


def room_players_query(room: Room, active_only: bool = True):
    query = Player.query.filter(or_(Player.room_id == room.id, Player.room_id.is_(None)))
    if active_only:
        query = query.filter(Player.active.is_(True))
    return query


def duplicate_player_exists(name: str, room_id: int | None, exclude_player_id: int | None = None) -> bool:
    query = Player.query.filter(func.lower(Player.name) == name.lower())
    if room_id is None:
        query = query.filter(Player.room_id.is_(None))
    else:
        query = query.filter(Player.room_id == room_id)
    if exclude_player_id is not None:
        query = query.filter(Player.id != exclude_player_id)
    return query.first() is not None


def selected_players_from_ids(player_ids: list[str], room: Room | None = None) -> list[Player]:
    clean_ids = []
    for raw_id in player_ids:
        try:
            clean_ids.append(int(raw_id))
        except ValueError:
            continue
    if not clean_ids:
        return []

    query = Player.query.filter(Player.id.in_(clean_ids), Player.active.is_(True))
    if room is not None:
        query = query.filter(or_(Player.room_id == room.id, Player.room_id.is_(None)))
    return query.all()


def balanced_draw(players: list[Player]) -> tuple[list[Player], list[Player]]:
    """Sorteia dois times com o menor desvio possível de média.

    Até 18 jogadores, testa combinações possíveis. Acima disso, usa amostragem aleatória.
    Para não ficar sempre igual, escolhe aleatoriamente entre as melhores combinações encontradas.
    """
    if len(players) < 2:
        raise ValueError("Selecione pelo menos 2 jogadores.")
    if len(players) % 2 != 0:
        raise ValueError("Selecione uma quantidade par de jogadores para formar dois times do mesmo tamanho.")

    shuffled = players[:]
    random.shuffle(shuffled)
    n = len(shuffled)
    team_size = n // 2

    def score_combo(combo_indexes: set[int]) -> tuple[float, set[int]]:
        team_x = [shuffled[i] for i in combo_indexes]
        team_y = [shuffled[i] for i in range(n) if i not in combo_indexes]
        avg_x = sum(p.rating for p in team_x) / len(team_x)
        avg_y = sum(p.rating for p in team_y) / len(team_y)
        return abs(avg_x - avg_y), combo_indexes

    candidates: list[tuple[float, set[int]]] = []

    if n <= 18:
        for combo in combinations(range(n), team_size):
            candidates.append(score_combo(set(combo)))
    else:
        attempts = max(4000, n * 250)
        seen: set[tuple[int, ...]] = set()
        for _ in range(attempts):
            combo = tuple(sorted(random.sample(range(n), team_size)))
            if combo in seen:
                continue
            seen.add(combo)
            candidates.append(score_combo(set(combo)))

    candidates.sort(key=lambda item: item[0])
    best_pool = candidates[: min(30, len(candidates))]
    _, chosen_indexes = random.choice(best_pool)

    team_x = [shuffled[i] for i in chosen_indexes]
    team_y = [shuffled[i] for i in range(n) if i not in chosen_indexes]

    if random.random() < 0.5:
        team_x, team_y = team_y, team_x

    return team_x, team_y


def apply_draw(match: Match, players: list[Player]) -> None:
    team_x, team_y = balanced_draw(players)
    match.assignments.clear()
    db.session.flush()
    for player in team_x:
        match.assignments.append(
            MatchAssignment(player_id=player.id, team="X", rating_snapshot=player.rating)
        )
    for player in team_y:
        match.assignments.append(
            MatchAssignment(player_id=player.id, team="Y", rating_snapshot=player.rating)
        )


def player_team(match: Match, player_id: int) -> str | None:
    for assignment in match.assignments:
        if assignment.player_id == player_id:
            return assignment.team
    return None


def elapsed_minute(match: Match) -> int:
    if not match.start_time:
        return 1
    elapsed_seconds = max(0, int((utcnow_naive() - match.start_time).total_seconds()))
    minute = elapsed_seconds // 60 + 1
    return max(1, min(8, minute))


def finish_match(match: Match) -> None:
    match.status = "finished"
    match.end_time = utcnow_naive()


def build_room_stats(room: Room, players: list[Player], matches: list[Match]) -> dict[str, list]:
    players_by_id: dict[int, Player] = {player.id: player for player in players}

    for match in matches:
        for assignment in match.assignments:
            players_by_id.setdefault(assignment.player_id, assignment.player)

    goal_counts: dict[int, int] = {}
    assist_counts: dict[int, int] = {}

    for match in matches:
        for event in match.events:
            goal_counts[event.scorer_id] = goal_counts.get(event.scorer_id, 0) + 1
            players_by_id.setdefault(event.scorer_id, event.scorer)
            if event.assist_id:
                assist_counts[event.assist_id] = assist_counts.get(event.assist_id, 0) + 1
                players_by_id.setdefault(event.assist_id, event.assist)

    top_scorers = sorted(
        [(players_by_id[player_id], total) for player_id, total in goal_counts.items() if player_id in players_by_id],
        key=lambda item: (-item[1], item[0].name.lower()),
    )
    top_assists = sorted(
        [(players_by_id[player_id], total) for player_id, total in assist_counts.items() if player_id in players_by_id],
        key=lambda item: (-item[1], item[0].name.lower()),
    )

    performance: dict[int, dict] = {
        player.id: {
            "player": player,
            "games": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "win_pct": 0.0,
        }
        for player in players_by_id.values()
    }

    for match in matches:
        if match.status != "finished":
            continue
        winner: str | None = None
        if match.score_x > match.score_y:
            winner = "X"
        elif match.score_y > match.score_x:
            winner = "Y"

        for assignment in match.assignments:
            row = performance.setdefault(
                assignment.player_id,
                {
                    "player": assignment.player,
                    "games": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "win_pct": 0.0,
                },
            )
            row["games"] += 1
            if winner is None:
                row["draws"] += 1
            elif assignment.team == winner:
                row["wins"] += 1
            else:
                row["losses"] += 1

    for row in performance.values():
        if row["games"]:
            row["win_pct"] = (row["wins"] / row["games"]) * 100

    performance_rows = sorted(
        performance.values(),
        key=lambda row: (-row["win_pct"], -row["wins"], -row["games"], row["player"].name.lower()),
    )

    return {
        "top_scorers": top_scorers,
        "top_assists": top_assists,
        "performance_rows": performance_rows,
    }


@app.route("/")
def index():
    rooms = Room.query.order_by(Room.created_at.desc()).all()
    players_count = Player.query.filter_by(active=True).count()
    matches_count = Match.query.count()
    return render_template("index.html", rooms=rooms, players_count=players_count, matches_count=matches_count)


@app.route("/s/<code>")
def room_by_code(code: str):
    normalized_code = normalize_room_code(code)
    room = Room.query.filter(func.upper(Room.code) == normalized_code).first_or_404()
    return redirect(url_for("room_detail", room_id=room.id))


@app.route("/rooms/join", methods=["POST"])
def join_room():
    code = normalize_room_code(request.form.get("code"))
    if not code:
        flash("Informe o código da sala.", "error")
        return redirect(url_for("index"))
    room = Room.query.filter(func.upper(Room.code) == code).first()
    if room is None:
        flash("Sala não encontrada.", "error")
        return redirect(url_for("index"))
    return redirect(url_for("room_detail", room_id=room.id))


@app.route("/rooms", methods=["POST"])
def create_room():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Informe o nome da sala.", "error")
        return redirect(url_for("index"))

    for _ in range(20):
        room = Room(name=name, code=generate_unique_room_code())
        db.session.add(room)
        try:
            db.session.commit()
            flash("Sala criada.", "success")
            return redirect(url_for("room_detail", room_id=room.id))
        except IntegrityError:
            db.session.rollback()

    flash("Não foi possível criar a sala. Tente novamente.", "error")
    return redirect(url_for("index"))


@app.route("/players", methods=["GET", "POST"])
def players():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        rating_raw = request.form.get("rating", "")
        if not name:
            flash("Informe o nome do jogador.", "error")
            return redirect(url_for("players"))
        try:
            rating = parse_rating(rating_raw)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("players"))

        if duplicate_player_exists(name, room_id=None):
            flash("Já existe um jogador global com esse nome.", "error")
            return redirect(url_for("players"))

        player = Player(name=name, rating=rating, active=True, room_id=None)
        db.session.add(player)
        try:
            db.session.commit()
            flash("Jogador cadastrado.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Já existe um jogador com esse nome.", "error")
        return redirect(url_for("players"))

    active_players = Player.query.filter_by(active=True).order_by(Player.name.asc()).all()
    inactive_players = Player.query.filter_by(active=False).order_by(Player.name.asc()).all()
    return render_template("players.html", active_players=active_players, inactive_players=inactive_players)


@app.route("/rooms/<int:room_id>/players", methods=["POST"])
def create_room_player(room_id: int):
    room = Room.query.get_or_404(room_id)
    name = request.form.get("name", "").strip()
    rating_raw = request.form.get("rating", "")

    if not name:
        flash("Informe o nome do jogador.", "error")
        return redirect(url_for("room_detail", room_id=room.id, _anchor="jogadores"))

    try:
        rating = parse_rating(rating_raw)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("room_detail", room_id=room.id, _anchor="jogadores"))

    if duplicate_player_exists(name, room_id=room.id):
        flash("Já existe um jogador com esse nome nessa sala.", "error")
        return redirect(url_for("room_detail", room_id=room.id, _anchor="jogadores"))

    player = Player(room_id=room.id, name=name, rating=rating, active=True)
    db.session.add(player)
    try:
        db.session.commit()
        flash("Jogador cadastrado.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Já existe um jogador com esse nome.", "error")

    return redirect(url_for("room_detail", room_id=room.id, _anchor="jogadores"))


@app.route("/players/<int:player_id>/edit", methods=["GET", "POST"])
def edit_player(player_id: int):
    player = Player.query.get_or_404(player_id)
    fallback = url_for("players")
    next_url = safe_next_url(request.args.get("next") or request.form.get("next"), fallback)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        rating_raw = request.form.get("rating", "")
        if not name:
            flash("Informe o nome do jogador.", "error")
            return redirect(url_for("edit_player", player_id=player.id, next=next_url))
        try:
            rating = parse_rating(rating_raw)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("edit_player", player_id=player.id, next=next_url))

        if duplicate_player_exists(name, room_id=player.room_id, exclude_player_id=player.id):
            flash("Já existe um jogador com esse nome.", "error")
            return redirect(url_for("edit_player", player_id=player.id, next=next_url))

        player.name = name
        player.rating = rating
        player.active = request.form.get("active") == "on"
        try:
            db.session.commit()
            flash("Jogador atualizado.", "success")
            return redirect(next_url)
        except IntegrityError:
            db.session.rollback()
            flash("Já existe um jogador com esse nome.", "error")
            return redirect(url_for("edit_player", player_id=player.id, next=next_url))
    return render_template("edit_player.html", player=player, next_url=next_url)


@app.route("/players/<int:player_id>/deactivate", methods=["POST"])
def deactivate_player(player_id: int):
    player = Player.query.get_or_404(player_id)
    next_url = safe_next_url(request.form.get("next"), url_for("players"))
    player.active = False
    db.session.commit()
    flash("Jogador removido da lista ativa. O histórico continua salvo.", "success")
    return redirect(next_url)


@app.route("/players/<int:player_id>/restore", methods=["POST"])
def restore_player(player_id: int):
    player = Player.query.get_or_404(player_id)
    next_url = safe_next_url(request.form.get("next"), url_for("players"))
    player.active = True
    db.session.commit()
    flash("Jogador restaurado.", "success")
    return redirect(next_url)


@app.route("/rooms/<int:room_id>")
def room_detail(room_id: int):
    room = Room.query.get_or_404(room_id)
    active_players = room_players_query(room, active_only=True).order_by(Player.name.asc()).all()
    matches = Match.query.filter_by(room_id=room.id).order_by(Match.scheduled_at.desc()).all()
    stats = build_room_stats(room, active_players, matches)
    now_local_value = utcnow_naive().strftime("%Y-%m-%dT%H:%M")
    share_path = url_for("room_by_code", code=room.code or "")
    return render_template(
        "room.html",
        room=room,
        players=active_players,
        matches=matches,
        stats=stats,
        now_local_value=now_local_value,
        share_path=share_path,
    )


@app.route("/rooms/<int:room_id>/matches", methods=["POST"])
def create_match(room_id: int):
    room = Room.query.get_or_404(room_id)
    selected_ids = request.form.getlist("player_ids")
    selected_players = selected_players_from_ids(selected_ids, room=room)

    try:
        scheduled_at = parse_datetime_local(request.form.get("scheduled_at"))
        if len(selected_players) < 2:
            raise ValueError("Selecione pelo menos 2 jogadores.")
        if len(selected_players) % 2 != 0:
            raise ValueError("Selecione uma quantidade par de jogadores para formar dois times iguais.")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("room_detail", room_id=room.id, _anchor="partidas"))

    title = request.form.get("title", "").strip()
    if not title:
        title = f"Partida {format_datetime(scheduled_at)}"

    match = Match(room_id=room.id, title=title, scheduled_at=scheduled_at)
    db.session.add(match)
    db.session.flush()
    apply_draw(match, selected_players)
    db.session.commit()

    flash("Partida criada e times sorteados.", "success")
    return redirect(url_for("match_detail", match_id=match.id))


@app.route("/matches/<int:match_id>")
def match_detail(match_id: int):
    match = Match.query.get_or_404(match_id)
    assignments_x = [a for a in match.assignments if a.team == "X"]
    assignments_y = [a for a in match.assignments if a.team == "Y"]
    assigned_ids = {a.player_id for a in match.assignments}
    available_query = room_players_query(match.room, active_only=True)
    if assigned_ids:
        available_query = available_query.filter(~Player.id.in_(assigned_ids))
    available_players = available_query.order_by(Player.name.asc()).all()
    avg_x = team_average(match.assignments, "X")
    avg_y = team_average(match.assignments, "Y")
    start_time_ms = None
    if match.start_time:
        start_time_ms = int(match.start_time.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return render_template(
        "match.html",
        match=match,
        assignments_x=assignments_x,
        assignments_y=assignments_y,
        available_players=available_players,
        avg_x=avg_x,
        avg_y=avg_y,
        start_time_ms=start_time_ms,
        goal_limit=GOAL_LIMIT,
    )


@app.route("/matches/<int:match_id>/start", methods=["POST"])
def start_match(match_id: int):
    match = Match.query.get_or_404(match_id)
    if match.status == "finished":
        flash("Essa partida já foi encerrada.", "error")
        return redirect(url_for("match_detail", match_id=match.id))
    if not match.assignments:
        flash("A partida precisa ter jogadores nos times.", "error")
        return redirect(url_for("match_detail", match_id=match.id))

    match.status = "in_progress"
    match.start_time = utcnow_naive()
    match.end_time = None
    db.session.commit()
    flash("Partida iniciada.", "success")
    return redirect(url_for("match_detail", match_id=match.id))


@app.route("/matches/<int:match_id>/finish", methods=["POST"])
def manual_finish(match_id: int):
    match = Match.query.get_or_404(match_id)
    if match.status != "finished":
        finish_match(match)
        db.session.commit()
        flash("Partida encerrada.", "success")
    return redirect(url_for("match_detail", match_id=match.id))


@app.route("/matches/<int:match_id>/finish-by-timer", methods=["POST"])
def finish_by_timer(match_id: int):
    match = Match.query.get_or_404(match_id)
    if match.status == "in_progress" and match.start_time:
        limit = match.start_time + timedelta(seconds=match.duration_seconds)
        if utcnow_naive() >= limit:
            finish_match(match)
            db.session.commit()
            return {"finished": True}
    return {"finished": False}


@app.route("/matches/<int:match_id>/goal", methods=["POST"])
def add_goal(match_id: int):
    match = Match.query.get_or_404(match_id)
    if match.status != "in_progress":
        flash("Só é possível registrar gol com a partida em andamento.", "error")
        return redirect(url_for("match_detail", match_id=match.id))

    team = request.form.get("team")
    if team not in TEAMS:
        abort(400)

    try:
        scorer_id = int(request.form.get("scorer_id", ""))
    except ValueError:
        flash("Escolha quem marcou o gol.", "error")
        return redirect(url_for("match_detail", match_id=match.id))

    assist_raw = request.form.get("assist_id", "")
    assist_id = None
    if assist_raw:
        try:
            assist_id = int(assist_raw)
        except ValueError:
            assist_id = None

    if player_team(match, scorer_id) != team:
        flash("O autor do gol precisa estar no time selecionado.", "error")
        return redirect(url_for("match_detail", match_id=match.id))
    if assist_id is not None:
        if assist_id == scorer_id:
            flash("Autor do gol e assistência não podem ser a mesma pessoa.", "error")
            return redirect(url_for("match_detail", match_id=match.id))
        if player_team(match, assist_id) != team:
            flash("A assistência precisa ser de alguém do mesmo time.", "error")
            return redirect(url_for("match_detail", match_id=match.id))

    if team == "X":
        match.score_x += 1
    else:
        match.score_y += 1

    event = GoalEvent(
        match_id=match.id,
        team=team,
        scorer_id=scorer_id,
        assist_id=assist_id,
        minute=elapsed_minute(match),
    )
    db.session.add(event)

    if match.score_x >= GOAL_LIMIT or match.score_y >= GOAL_LIMIT:
        finish_match(match)
        flash("Gol registrado. Partida encerrada por limite de gols.", "success")
    else:
        flash("Gol registrado.", "success")

    db.session.commit()
    return redirect(url_for("match_detail", match_id=match.id))


@app.route("/matches/<int:match_id>/reroll", methods=["POST"])
def reroll_match(match_id: int):
    match = Match.query.get_or_404(match_id)
    if match.status != "scheduled" or match.events:
        flash("Só é possível sortear novamente antes de iniciar a partida e sem gols registrados.", "error")
        return redirect(url_for("match_detail", match_id=match.id))

    players_to_draw = [assignment.player for assignment in match.assignments if assignment.player.active]
    try:
        apply_draw(match, players_to_draw)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("match_detail", match_id=match.id))
    db.session.commit()
    flash("Times sorteados novamente.", "success")
    return redirect(url_for("match_detail", match_id=match.id))


@app.route("/matches/<int:match_id>/move", methods=["POST"])
def move_player(match_id: int):
    match = Match.query.get_or_404(match_id)
    if match.status == "finished":
        flash("Partida encerrada não pode ser alterada.", "error")
        return redirect(url_for("match_detail", match_id=match.id))

    try:
        assignment_id = int(request.form.get("assignment_id", ""))
    except ValueError:
        flash("Escolha um jogador.", "error")
        return redirect(url_for("match_detail", match_id=match.id))

    new_team = request.form.get("team")
    if new_team not in TEAMS:
        flash("Escolha um time válido.", "error")
        return redirect(url_for("match_detail", match_id=match.id))

    assignment = MatchAssignment.query.filter_by(id=assignment_id, match_id=match.id).first_or_404()
    assignment.team = new_team
    db.session.commit()
    flash("Jogador movido de time.", "success")
    return redirect(url_for("match_detail", match_id=match.id))


@app.route("/matches/<int:match_id>/substitute", methods=["POST"])
def substitute_player(match_id: int):
    match = Match.query.get_or_404(match_id)
    if match.status == "finished":
        flash("Partida encerrada não pode ser alterada.", "error")
        return redirect(url_for("match_detail", match_id=match.id))

    try:
        assignment_id = int(request.form.get("assignment_id", ""))
        incoming_player_id = int(request.form.get("incoming_player_id", ""))
    except ValueError:
        flash("Escolha o jogador que sai e o jogador que entra.", "error")
        return redirect(url_for("match_detail", match_id=match.id))

    assignment = MatchAssignment.query.filter_by(id=assignment_id, match_id=match.id).first_or_404()
    incoming = room_players_query(match.room, active_only=True).filter(Player.id == incoming_player_id).first_or_404()

    already_assigned = MatchAssignment.query.filter_by(match_id=match.id, player_id=incoming.id).first()
    if already_assigned:
        flash("Esse jogador já está em um dos times da partida.", "error")
        return redirect(url_for("match_detail", match_id=match.id))

    assignment.player_id = incoming.id
    assignment.rating_snapshot = incoming.rating
    db.session.commit()
    flash("Substituição realizada.", "success")
    return redirect(url_for("match_detail", match_id=match.id))


@app.route("/matches/<int:match_id>/rematch", methods=["POST"])
def rematch(match_id: int):
    old_match = Match.query.get_or_404(match_id)
    players_to_draw = [assignment.player for assignment in old_match.assignments if assignment.player.active]
    if len(players_to_draw) < 2:
        flash("Não há jogadores ativos suficientes para criar revanche.", "error")
        return redirect(url_for("match_detail", match_id=old_match.id))

    new_match = Match(
        room_id=old_match.room_id,
        title=f"Revanche de {old_match.title}",
        scheduled_at=utcnow_naive(),
    )
    db.session.add(new_match)
    db.session.flush()
    try:
        apply_draw(new_match, players_to_draw)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("match_detail", match_id=old_match.id))
    db.session.commit()
    flash("Revanche criada com os mesmos jogadores e novo sorteio.", "success")
    return redirect(url_for("match_detail", match_id=new_match.id))


def ensure_schema() -> None:
    """Cria tabelas novas e atualiza bancos SQLite antigos do MVP inicial."""
    db.create_all()
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    if "room" in tables:
        room_columns = {column["name"] for column in inspector.get_columns("room")}
        if "code" not in room_columns:
            db.session.execute(text("ALTER TABLE room ADD COLUMN code VARCHAR(12)"))
            db.session.commit()

        rooms_without_code = Room.query.filter(or_(Room.code.is_(None), Room.code == "")).all()
        for room in rooms_without_code:
            room.code = generate_unique_room_code()
        db.session.commit()

        try:
            db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_room_code_unique ON room (code)"))
            db.session.commit()
        except Exception:
            db.session.rollback()

    if "player" in tables:
        player_columns = {column["name"] for column in inspect(db.engine).get_columns("player")}
        if "room_id" not in player_columns:
            db.session.execute(text("ALTER TABLE player ADD COLUMN room_id INTEGER"))
            db.session.commit()


with app.app_context():
    ensure_schema()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
