"""Autograder scripts."""
from rich.console import Console
from rich.theme import Theme
from typer import Typer

theme = Theme({
    "success": "green",
    "warning": "orange3",
    "error": "red",
    "attention": "magenta2",
    "heading": "blue",
    "info": "dim cyan",
})
console = Console(theme=theme)
app = Typer(pretty_exceptions_show_locals=True)

@app.command()
def main():
    console.print("yay!")


if __name__ == "__main__":
    app()
