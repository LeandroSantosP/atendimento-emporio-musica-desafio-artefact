"""Interface de linha de comando do agente."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown

from .agent import Agent
from .history import load_history, save_history


console = Console()


def _print_answer(answer: str) -> None:
    """Renderiza Markdown no terminal em vez de mostrar os marcadores crus."""
    console.print(Markdown(answer))


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    agent = Agent()
    history = load_history()

    console.print("[bold]Empório da Música — Atendimento[/bold]")
    console.print(
        "Digite [bold]/novo[/bold] para limpar a conversa ou "
        "[bold]/sair[/bold] para encerrar.\n"
    )

    while True:
        try:
            user_message = console.input("[bold cyan]Você:[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nAté logo!")
            return

        if not user_message:
            continue
        if user_message == "/sair":
            console.print("Até logo!")
            return
        if user_message == "/novo":
            history = []
            save_history(history)
            console.print("Conversa reiniciada.")
            continue

        answer = agent.respond(history, user_message)
        history.extend(
            [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": answer},
            ]
        )
        save_history(history)

        console.print("\n[bold green]Empório da Música:[/bold green]")
        _print_answer(answer)
        console.print()


if __name__ == "__main__":
    main()
