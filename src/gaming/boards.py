"""Small games that live in the app. No network, no install."""

from __future__ import annotations

import random
import re

WORDS = ["forge", "mercy", "covenant", "steam", "anvil", "ledger", "signal", "harbor"]

TRIVIA = [
    {"prompt": "The golden rule, in plain words.", "answer": "treat others the way you want to be treated"},
    {"prompt": "Which tier stays useful with no GPU?", "answer": "lite"},
    {"prompt": "Where does the admin secret live?", "answer": "environment or local file"},
]


class BoardTable:
    def __init__(self, rng=None):
        self.rng = rng or random.Random(0)
        self.word = ""
        self.mask = []
        self.guesses = []
        self.trivia_index = 0
        self.board = [" "] * 9
        self.turn = "X"
        self.winner = ""

    def new_word(self) -> dict:
        self.word = self.rng.choice(WORDS)
        self.mask = ["_"] * len(self.word)
        self.guesses = []
        return self.word_state()

    def word_state(self) -> dict:
        return {
            "mask": " ".join(self.mask) if self.mask else "",
            "guesses": list(self.guesses),
            "done": bool(self.word) and "_" not in self.mask,
            "word": self.word if self.mask and "_" not in self.mask else "",
        }

    def guess_letter(self, letter: str) -> dict:
        if not self.word:
            self.new_word()
        letter = (letter or "").strip().lower()[:1]
        if not letter or not letter.isalpha():
            return {"ok": False, "error": "Give me one letter.", **self.word_state()}
        if letter in self.guesses:
            return {"ok": True, "already": True, **self.word_state()}
        self.guesses.append(letter)
        for index, char in enumerate(self.word):
            if char == letter:
                self.mask[index] = letter
        return {"ok": True, **self.word_state()}

    def trivia(self) -> dict:
        item = TRIVIA[self.trivia_index % len(TRIVIA)]
        return {"prompt": item["prompt"], "index": self.trivia_index}

    def answer_trivia(self, text: str) -> dict:
        item = TRIVIA[self.trivia_index % len(TRIVIA)]
        expected = item["answer"]
        given = re.sub(r"[^a-z0-9 ]", "", (text or "").lower())
        ok = expected in given or given in expected
        if ok:
            self.trivia_index += 1
        return {"ok": ok, "expected": expected if not ok else "", "next": self.trivia()}

    def tic_state(self) -> dict:
        return {"board": list(self.board), "turn": self.turn, "winner": self.winner}

    def _winner(self) -> str:
        lines = (
            (0, 1, 2), (3, 4, 5), (6, 7, 8),
            (0, 3, 6), (1, 4, 7), (2, 5, 8),
            (0, 4, 8), (2, 4, 6),
        )
        for a, b, c in lines:
            if self.board[a] != " " and self.board[a] == self.board[b] == self.board[c]:
                return self.board[a]
        if " " not in self.board:
            return "draw"
        return ""

    def tic_move(self, cell: int, mark: str = "X") -> dict:
        if self.winner:
            return {"ok": False, "error": "Game over.", **self.tic_state()}
        if mark != self.turn:
            return {"ok": False, "error": f"It is {self.turn}'s turn.", **self.tic_state()}
        if cell < 0 or cell > 8 or self.board[cell] != " ":
            return {"ok": False, "error": "That square is taken.", **self.tic_state()}
        self.board[cell] = mark
        self.winner = self._winner()
        if not self.winner:
            self.turn = "O" if mark == "X" else "X"
        if self.turn == "O" and not self.winner:
            self._oxidus_tic()
        return {"ok": True, **self.tic_state()}

    def _oxidus_tic(self) -> None:
        for cell in range(9):
            if self.board[cell] == " ":
                self.board[cell] = "O"
                self.winner = self._winner()
                self.turn = "X" if not self.winner else "O"
                return
