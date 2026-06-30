# Sorteador de Futebol

Site em Python/Flask para organizar futebol entre amigos com visual verde escuro, salas por código, sorteio equilibrado e histórico de partidas.

## Funções incluídas

- Criação de salas com código público, como `BDE102`.
- Cadastro de jogadores dentro da sala com nota de 0 a 10.
- Tela da sala com abas: Jogadores, Partidas e Estatísticas.
- Sorteio de dois times com médias próximas.
- Botão para sortear novamente antes do início da partida.
- Alterações manuais nas escalações.
- Substituição usando apenas jogadores cadastrados.
- Timer visual de 8 minutos.
- Encerramento por tempo ou quando um time chega a 3 gols.
- Registro de autor do gol e assistência.
- Estatísticas de artilheiros, assistências e aproveitamento.
- Preparado para deploy com `gunicorn`, `Procfile`, `SECRET_KEY` e `DATABASE_URL` por variável de ambiente.

## Rodar localmente no Windows

No terminal, dentro da pasta do projeto:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Acesse:

```text
http://127.0.0.1:5000
```

Se preferir ativar o ambiente virtual:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Rodar localmente no macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Deploy

Para hospedar em um serviço que aceite apps Python:

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- Variável `SECRET_KEY`: uma chave grande e aleatória.
- Variável `DATABASE_URL`: conexão do banco.

O app usa SQLite local se `DATABASE_URL` não existir. Para deixar no ar de verdade com dados persistentes, use PostgreSQL no host e coloque a URL na variável `DATABASE_URL`.

## Banco de dados

Na primeira execução, as tabelas são criadas automaticamente. Se você já estava usando a versão anterior, o app tenta adicionar as colunas novas `room.code` e `player.room_id` automaticamente.

## Observações

- Sem login, quem tiver o link ou código da sala consegue mexer nos dados da sala.
- O sorteio exige quantidade par de jogadores para formar times com o mesmo tamanho.
- A nota usada em uma escalação fica salva na partida, mesmo que a nota do jogador mude depois.
